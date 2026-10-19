"""
audio_engine.py — Onix FM Digital  v4.0  (BASS Edition)
================================================================================

Motor de audio profesional para emision 24/7.

BACKENDS DISPONIBLES (en orden de preferencia):
    1. BASS.dll  -- Motor profesional. Gratis solo para uso NO comercial;
                   para emision comercial se requiere licencia:
                   https://www.un4seen.com/bass.html
                   · Windows: bass.dll      (en la carpeta del proyecto)
                   · Linux:   libbass.so    (misma carpeta o /usr/lib)
                   · macOS:   libbass.dylib (misma carpeta)
    2. mock      -- Simulacion sin hardware (reloj de pared, sin sonido).
                   Solo para desarrollo/pruebas y para el contenedor Docker.

ARQUITECTURA DE HILOS:
    Un unico hilo (AudioEngineLoop) es dueño de todo el estado de reproduccion.
    La API nunca toca los canales directamente: encola comandos (play, skip,
    stop, efecto) que el loop ejecuta en su siguiente tick (50 ms). Asi no hay
    carreras entre la API, el watchdog y el propio motor.

MEZCLA (CROSSFADE):
    El loop vigila la posicion del canal activo. Al llegar al punto de mezcla
    (cue_mezcla > outro > duracion - crossfade_seg) arranca la siguiente pista
    en el otro canal y desliza los volumenes con BASS_ChannelSlideAttribute
    (rampa nativa dentro del mixer, sin time.sleep en Python).

PAUTAS (TANDAS):
    Cada pocos segundos se consulta la tabla `pautas`. Las que tienen marcada
    la celda dia-hora actual en su matriz se reparten entre las tandas de la
    hora (config `tandas_minutos`, p. ej. "15,45"), separando anunciantes del
    mismo rubro. Al llegar el minuto de una tanda, sus cuñas entran juntas a
    una cola prioritaria y salen en bloque en el siguiente cambio de pista.
    Cada pauta sale una sola vez por hora (historial_emision.slot) y nunca mas
    de `menciones_diarias` veces en el dia.

RESPALDO:
    En modo automatico, si la rotacion no devuelve nada (biblioteca vacia,
    BD caida, archivos rotos) suena la carpeta media/respaldo/ en vez de
    quedar en silencio.

WATCHDOG:
    Hilo independiente que verifica cada 2 segundos si el canal activo sigue
    sonando y avanzando. Solo detecta y emite eventos; la recuperacion la
    ejecuta el loop del motor.

COLUMNAS BD (fuente de verdad: scripts/init_db.py):
    audios: id, titulo, artista, archivo_path, duracion, subgenero (cat1),
            categoria (cat2), cat3, genero_vocal (voz), cue_inicio, cue_mezcla,
            outro, ultima_reproduccion, veces_reproducido, activo ...
"""

import ctypes
import json
import logging
import os
import platform
import queue
import random
import re
import threading
import time
import unicodedata
from collections import deque
from ctypes import c_bool, c_char_p, c_double, c_float
from ctypes import c_int, c_int64, c_uint32, c_void_p, c_wchar_p
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("AudioEngine")

# Rutas compartidas con api_server.py (misma BD, misma carpeta de medios)
from backend.paths import AUDIO_EXTS, DB_PATH, RESPALDO_DIR, resolve_media
from backend.audio.loudness import ganancia_db
from backend.audio.planner import Planner, duracion_efectiva, calcular_tip, TIP_MODOS
from backend.audio.eventbus import EventBus  # noqa: F401  (re-export)
from backend.version import VERSION

_THIS_FILE = Path(__file__).resolve()

# Tipos de audio (audios.tipo_audio). Solo 'Musica' entra en la rotacion
# automatica; las cuñas salen unicamente por la programacion de pautas.
TIPO_MUSICA = "Musica"
TIPO_CUNA   = "Cuña"
# Voice track (locución grabada o hecha con voz de IA): entra SOBRE el final de la
# canción anterior, con esa música bajada (talk-over), y la siguiente arranca al terminar la voz.
TIPO_VOZ    = "Locucion"

# Normalizacion: la ganancia de cada audio (ver loudness.py) se aplica como
# volumen del canal. Se permite subir hasta +6 dB (volumen 2.0) y nunca por
# encima de un pico de -1 dBTP.
MAX_BOOST_DB = 6.0
VOL_MAX      = 10 ** (MAX_BOOST_DB / 20)


# ══════════════════════════════════════════════════════════════════════════════
# Tandas de pautas -- funciones puras (se prueban sin motor)
# ══════════════════════════════════════════════════════════════════════════════
def parse_minutos(valor: str) -> list[int]:
    """'15, 45' -> [15, 45]. Sin minutos validos la hora tiene una sola tanda en :00."""
    minutos = {int(x) for x in re.split(r"[,;\s]+", valor or "")
               if x.isdigit() and 0 <= int(x) <= 59}
    return sorted(minutos) or [0]


def cargar_grilla(texto: str) -> dict:
    """Config `tandas_grilla` (JSON) -> {"dia-hora": {...}}. Texto roto = sin grilla."""
    try:
        g = json.loads(texto or "{}")
    except ValueError:
        return {}
    return {k: v for k, v in g.items() if isinstance(v, dict)} if isinstance(g, dict) else {}


def tandas_de_hora(grilla: dict, dia: int, hora: int, minutos: list[int],
                   inmediatas: list[int], cierre: list[int]) -> tuple[list[int], list[int], list[int]]:
    """
    Tandas de una hora concreta (como los «Breaks comerciales» de Jazler, que
    varían por hora y por día). Clave "dia-hora", dia 0=Dom…6=Sab, igual que la
    matriz de las pautas. Sin entrada valen los minutos generales; con
    {"off": true} esa hora no tiene tandas; si no, la entrada manda:
    {"minutos": "15,45", "inmediatas": "15", "cierre": ""}.
    """
    e = (grilla or {}).get(f"{dia}-{hora}")
    if not isinstance(e, dict):
        return list(minutos), list(inmediatas), list(cierre)
    if e.get("off"):
        return [], [], []
    m = parse_minutos(str(e["minutos"])) if str(e.get("minutos") or "").strip() else list(minutos)

    def lista(clave, defecto):
        if clave not in e:
            return [x for x in defecto if x in m]
        txt = str(e.get(clave) or "").strip()
        return [x for x in parse_minutos(txt) if x in m] if txt else []
    return m, lista("inmediatas", inmediatas), lista("cierre", cierre)




def leer_ajustes(conn, fecha: str, hora: int) -> tuple[dict[int, list[int]], set[int]]:
    """
    Ajustes a mano del operador (Admin › Comerciales › Tandas del día) para una
    hora de un día: {minuto: [pauta_id en orden]} y las cuñas quitadas ese día.
    """
    bloques: dict[int, list[int]] = {}
    quitadas: set[int] = set()
    # Los ajustes son un extra del operador: si la base no los puede leer, las tandas salen con el reparto automático.
    # Nunca deben dejar al aire sin publicidad.
    try:
        for m, ids in conn.execute("SELECT minuto, ids FROM tanda_ajustes WHERE fecha = ? AND hora = ?", (fecha, hora)):
            try:
                bloques[int(m)] = [int(i) for i in json.loads(ids or "[]")]
            except (TypeError, ValueError):
                continue
        quitadas = {int(r[0]) for r in conn.execute(
            "SELECT pauta_id FROM tanda_quitadas WHERE fecha = ? AND hora = ?", (fecha, hora))}
    except Exception as exc:
        log.warning("[Pautas] No se pudieron leer los ajustes de tandas (se usa el reparto automático): %s", exc)
        return {}, set()
    return bloques, quitadas


def aplicar_ajustes(plan: dict[int, list[int]], bloques: dict[int, list[int]]) -> dict[int, list[int]]:
    """
    Aplica el orden a mano sobre el reparto automático: las cuñas de cada bloque
    ajustado van primero y en ese orden (aunque el reparto las pusiera en otra
    tanda); las demás siguen donde estaban, detrás. Un bloque ajustado cuyo
    minuto ya no existe (cambió la grilla) se ignora.
    """
    bloques = {m: ids for m, ids in bloques.items() if m in plan}
    todos = {i for ids in plan.values() for i in ids}
    fijadas = {i for ids in bloques.values() for i in ids if i in todos}
    nuevo = {}
    for m, ids in plan.items():
        resto = [i for i in ids if i not in fijadas]
        propias = list(dict.fromkeys(i for i in bloques.get(m, []) if i in todos))
        nuevo[m] = propias + resto
    return nuevo


CURVA_FADE_DEFECTO = (1.0, 0.70, 0.40, 0.30, 0.16)


def parse_curva_fade(texto: str) -> list[float]:
    """'100,70,40,30,16' -> [1.0, .7, .4, .3, .16] (volumen en % repartido en partes iguales del fade). Vacío o roto = defecto."""
    try:
        v = [max(0.0, min(100.0, float(x))) / 100 for x in re.split(r"[,;\s]+", (texto or "").strip()) if x != ""]
    except ValueError:
        return list(CURVA_FADE_DEFECTO)
    return v if 2 <= len(v) <= 12 else list(CURVA_FADE_DEFECTO)


def inicio_tanda(minuto: int, duracion: float, cierre: bool) -> float:
    """
    Segundos desde el inicio de la hora en que arranca la tanda.
    Normal: arranca en su minuto. Al cierre (costumbre venezolana): el bloque
    TERMINA en su minuto (0 = en punto de la hora siguiente) y arranca antes
    según lo que dure, p. ej. 10 min de cuñas → 3:50.
    """
    if not cierre:
        return minuto * 60.0
    fin = (60 if minuto == 0 else minuto) * 60.0
    return max(0.0, fin - max(0.0, duracion))


def version_rotada(versiones: list[int], emitidas: int) -> Optional[int]:
    """Spot con varias versiones: A, B, C, A, B… según cuántas veces ya salió."""
    return versiones[emitidas % len(versiones)] if versiones else None


def norm_rubro(texto) -> str:
    """Rubro en forma comparable: sin espacios de más, sin tildes y en mayúsculas ('Cafetería ' == 'CAFETERIA')."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(t.split()).upper()


def _rubro(pauta: dict) -> str:
    return norm_rubro(pauta.get("rubro"))


# Pares de rubros que pueden ir en la misma tanda pero NO una cuña tras otra (Restaurante y Cafetería).
CONFLICTOS_RUBROS: set = set()


def fijar_conflictos_rubros(pares) -> None:
    global CONFLICTOS_RUBROS
    CONFLICTOS_RUBROS = {frozenset((norm_rubro(a), norm_rubro(b))) for a, b in pares
                         if norm_rubro(a) and norm_rubro(b) and norm_rubro(a) != norm_rubro(b)}


def rubros_chocan(a: str, b: str) -> bool:
    """Mismo rubro o rubros marcados como incompatibles: no deben sonar seguidos."""
    return bool(a) and bool(b) and (a == b or frozenset((a, b)) in CONFLICTOS_RUBROS)


def leer_conflictos_rubros(conn) -> list[tuple[str, str]]:
    try:
        return [(r[0], r[1]) for r in conn.execute("SELECT a, b FROM comerciales_conflictos_rubros")]
    except Exception:
        return []



SEPARAR_VOCES = True      # regla «voces de mujer no se pegan» (config regla_voces_mujer)


def fijar_regla_voces(activa: bool) -> None:
    global SEPARAR_VOCES
    SEPARAR_VOCES = bool(activa)


def es_voz_mujer(voz) -> bool:
    """'Mujer', 'MUJER', 'Femenina'… (Dúo, Grupo, Instrumental, Hombre y vacío no cuentan como mujer)."""
    v = unicodedata.normalize("NFKD", str(voz or "")).encode("ascii", "ignore").decode().strip().upper()
    return v.startswith(("MUJER", "FEMEN", "FEMALE", "WOMAN")) or v == "F"


def ordenar_tanda(pautas: list[dict]) -> list[dict]:
    """
    Orden de salida dentro de una tanda: las fijadas "primero" abren y las
    fijadas "último" cierran (como el alfiler de Jazler); después van las de
    orden manual (el que armó programación, tal cual) y el resto respeta el
    orden recibido (prioridad primero) evitando dos cuñas seguidas del mismo
    rubro y dos voces de mujer seguidas (regla de las radios venezolanas). Las
    reglas ceden si no hay cómo cumplirlas: una cuña nunca se queda sin salir.
    """
    pos = lambda p: int(p.get("posicion") or 0)
    primeras = [p for p in pautas if pos(p) == 1]
    ultimas = [p for p in pautas if pos(p) == 2]
    medio = [p for p in pautas if pos(p) not in (1, 2)]
    primeras += sorted((p for p in medio if p.get("orden") is not None), key=lambda p: p["orden"])
    pendientes, orden = [p for p in medio if p.get("orden") is None], []
    mujer = lambda p: SEPARAR_VOCES and es_voz_mujer(p.get("voz"))
    while pendientes:
        previo_p = orden[-1] if orden else (primeras[-1] if primeras else None)
        prev_rubro = _rubro(previo_p) if previo_p else ""
        prev_mujer = bool(previo_p) and mujer(previo_p)

        def rubro_ok(p):
            return not rubros_chocan(prev_rubro, _rubro(p))

        def voz_ok(p):
            return not (prev_mujer and mujer(p))

        def viable(i):
            """Con esta cuña, ¿lo que queda se puede acomodar sin juntar dos mujeres?"""
            if not SEPARAR_VOCES:
                return True
            resto = pendientes[:i] + pendientes[i + 1:]
            m = sum(1 for q in resto if mujer(q))
            return m <= (len(resto) - m) + (0 if mujer(pendientes[i]) else 1)

        rango = range(len(pendientes))
        idx = next((i for i in rango if rubro_ok(pendientes[i]) and voz_ok(pendientes[i]) and viable(i)), None)
        if idx is None:
            idx = next((i for i in rango if rubro_ok(pendientes[i]) and voz_ok(pendientes[i])), None)
        if idx is None:
            idx = next((i for i in rango if voz_ok(pendientes[i])), None)       # la voz pesa más que el rubro
        if idx is None:
            idx = next((i for i in rango if rubro_ok(pendientes[i])), 0)
        orden.append(pendientes.pop(idx))

    resultado = primeras + orden + ultimas

    # ── Post-validación estricta y reordenamiento de seguridad (Swapping) ──
    # Si tras armar la lista quedó algún choque consecutivo entre piezas adyacentes
    # (por ejemplo con 'primeras' o 'ultimas'), intentamos intercambiar con un
    # vecino no conflictivo para subsanarlo.
    def hay_choque(a_p, b_p):
        if not a_p or not b_p:
            return False
        r_a, r_b = _rubro(a_p), _rubro(b_p)
        if rubros_chocan(r_a, r_b):
            return True
        if SEPARAR_VOCES and mujer(a_p) and mujer(b_p):
            return True
        return False

    # Iterar hasta 3 pasadas para corregir cualquier adyacencia
    for _ in range(3):
        hubo_cambio = False
        for i in range(len(resultado) - 1):
            if hay_choque(resultado[i], resultado[i + 1]):
                # Buscar un elemento j más adelante con el que intercambiar
                # respetando las restricciones de 'posicion' (primeras y ultimas)
                pos_i = pos(resultado[i + 1])
                for j in range(i + 2, len(resultado)):
                    pos_j = pos(resultado[j])
                    if pos_i in (1, 2) or pos_j in (1, 2):
                        continue
                    # Verificar si al mover resultado[j] a i+1 no choca con resultado[i] ni con resultado[i+2]
                    izq_ok = not hay_choque(resultado[i], resultado[j])
                    der_ok = (i + 2 >= len(resultado)) or (not hay_choque(resultado[j], resultado[i + 2]))
                    if izq_ok and der_ok:
                        resultado[i + 1], resultado[j] = resultado[j], resultado[i + 1]
                        hubo_cambio = True
                        break
        if not hubo_cambio:
            break

    return resultado



def planificar_tandas(pautas: list[dict], minutos: list[int]) -> dict[int, list[dict]]:
    """
    Reparte las pautas de una hora entre sus tandas. `pautas` llega ordenada
    por prioridad. Cada pauta va a la tanda con menos anunciantes de su mismo
    rubro y, a igualdad, a la menos cargada (y la mas temprana).
    """
    if not minutos:
        return {}               # hora sin tandas (grilla por día y hora)
    tandas: dict[int, list[dict]] = {m: [] for m in minutos}
    for p in pautas:
        rubro = _rubro(p)

        def costo(m: int):
            mismos = sum(1 for q in tandas[m] if rubro and _rubro(q) == rubro)
            return (mismos, len(tandas[m]), m)

        tandas[min(minutos, key=costo)].append(p)
    return {m: ordenar_tanda(lst) for m, lst in tandas.items()}


from backend.database import get_connection as _db_conn



# ══════════════════════════════════════════════════════════════════════════════
# BASS.dll -- Constantes
# ══════════════════════════════════════════════════════════════════════════════
BASS_ACTIVE_STOPPED  = 0
BASS_ACTIVE_PLAYING  = 1
BASS_ACTIVE_STALLED  = 2
BASS_ACTIVE_PAUSED   = 3

BASS_ATTRIB_VOL      = 2
BASS_POS_BYTE        = 0

# PRESCAN = escanea el archivo completo al cargar para obtener duracion exacta.
BASS_STREAM_PRESCAN  = 0x20000
BASS_SAMPLE_FLOAT    = 0x100
# UNICODE = la ruta se pasa como UTF-16 (obligatorio en Windows para rutas con
# tildes o eñes; sin este flag BASS interpreta la ruta en la pagina ANSI).
BASS_UNICODE         = 0x80000000

BASS_ERROR_ALREADY   = 14
BASS_CONFIG_UNICODE  = 42      # nombres de dispositivos en UTF-8 (Windows)
BASS_LEVEL_STEREO    = 2
BASS_LEVEL_VOLPAN    = 8       # el nivel incluye el volumen del canal (fades, ducking)
BASS_DEVICE_ENABLED  = 1
BASS_DEVICE_DEFAULT  = 2


class BASS_DEVICEINFO(ctypes.Structure):
    _fields_ = [("name", c_char_p), ("driver", c_char_p), ("flags", c_uint32)]


def _decode_name(raw: Optional[bytes]) -> str:
    if not raw:
        return ""
    for enc in ("utf-8", "mbcs", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


# ══════════════════════════════════════════════════════════════════════════════
# Deteccion del backend de audio disponible
# ══════════════════════════════════════════════════════════════════════════════
def _detect_bass_lib() -> Optional[str]:
    """
    Busca la libreria BASS en el directorio del proyecto y en rutas del sistema.
    Retorna la ruta si la encuentra y puede cargarla, None si no.
    """
    system   = platform.system()
    base_dir = _THIS_FILE.parent.parent.parent

    candidates = {
        "Windows": [str(base_dir / "bass.dll"),
                    str(base_dir / "lib" / "bass.dll"),
                    "bass.dll"],
        "Linux":   [str(base_dir / "libbass.so"),
                    "/usr/lib/libbass.so",
                    "libbass.so"],
        "Darwin":  [str(base_dir / "libbass.dylib"),
                    "libbass.dylib"],
    }.get(system, [])

    for path in candidates:
        try:
            (ctypes.WinDLL(path) if system == "Windows" else ctypes.CDLL(path))
            log.info("[BASS] Libreria encontrada: %s", path)
            return path
        except OSError:
            continue
    return None


# ONIX_AUDIO_BACKEND=mock fuerza el simulador aunque BASS este disponible.
BASS_LIB_PATH = None if os.getenv("ONIX_AUDIO_BACKEND") == "mock" else _detect_bass_lib()

if BASS_LIB_PATH:
    BACKEND = "bass"
else:
    BACKEND = "mock"
    log.warning("[ENGINE] BASS no disponible -> modo mock (sin sonido).")


# ══════════════════════════════════════════════════════════════════════════════
# BASS Backend -- Motor profesional via ctypes
# ══════════════════════════════════════════════════════════════════════════════
class BASSBackend:
    """
    Wrapper sobre bass.dll via ctypes.

    Cada 'canal logico' (0=A, 1=B, 2-7=efx, 8=preescucha) corresponde a un
    stream handle de BASS. Los streams se crean al cargar un archivo y se
    liberan al detener el canal o al cargar uno nuevo.

    VARIAS SALIDAS: cada stream se crea en la tarjeta que se pida
    (BASS_SetDevice antes de crearlo). Las tarjetas se inicializan la
    primera vez que se usan, asi el AIRE y la PREESCUCHA pueden ir por
    salidas distintas de la PC.
    """

    def __init__(self):
        self._windows = platform.system() == "Windows"
        self._bass = (ctypes.WinDLL(BASS_LIB_PATH) if self._windows
                      else ctypes.CDLL(BASS_LIB_PATH))

        # Definir tipos de argumentos y retorno para cada funcion BASS.
        # HSTREAM/DWORD = 32 bits, QWORD = 64 bits. Sin esto ctypes trunca.
        b = self._bass

        b.BASS_Init.restype  = c_bool
        b.BASS_Init.argtypes = [c_int, c_uint32, c_uint32, c_void_p, c_void_p]

        b.BASS_Free.restype = c_bool

        b.BASS_StreamCreateFile.restype  = c_uint32
        b.BASS_StreamCreateFile.argtypes = [
            c_bool,                                   # mem: False = archivo en disco
            c_wchar_p if self._windows else c_char_p, # ruta
            c_int64,                                  # offset en el archivo
            c_int64,                                  # longitud (0 = todo)
            c_uint32,                                 # flags
        ]

        b.BASS_StreamFree.restype  = c_bool
        b.BASS_StreamFree.argtypes = [c_uint32]

        b.BASS_ChannelPlay.restype  = c_bool
        b.BASS_ChannelPlay.argtypes = [c_uint32, c_bool]
        b.BASS_ChannelPause.restype  = c_bool
        b.BASS_ChannelPause.argtypes = [c_uint32]

        b.BASS_ChannelStop.restype  = c_bool
        b.BASS_ChannelStop.argtypes = [c_uint32]

        b.BASS_ChannelIsActive.restype  = c_uint32
        b.BASS_ChannelIsActive.argtypes = [c_uint32]

        b.BASS_ChannelSetAttribute.restype  = c_bool
        b.BASS_ChannelSetAttribute.argtypes = [c_uint32, c_uint32, c_float]

        # Rampa de volumen nativa: (handle, atributo, valor final, milisegundos)
        b.BASS_ChannelSlideAttribute.restype  = c_bool
        b.BASS_ChannelSlideAttribute.argtypes = [c_uint32, c_uint32, c_float, c_uint32]

        # GetLength / GetPosition retornan QWORD (64-bit); -1 = error
        b.BASS_ChannelGetLength.restype  = c_int64
        b.BASS_ChannelGetLength.argtypes = [c_uint32, c_uint32]

        b.BASS_ChannelGetPosition.restype  = c_int64
        b.BASS_ChannelGetPosition.argtypes = [c_uint32, c_uint32]

        b.BASS_ChannelSetPosition.restype  = c_bool
        b.BASS_ChannelSetPosition.argtypes = [c_uint32, c_int64, c_uint32]

        b.BASS_ChannelBytes2Seconds.restype  = c_double
        b.BASS_ChannelBytes2Seconds.argtypes = [c_uint32, c_int64]

        b.BASS_ChannelSeconds2Bytes.restype  = c_int64
        b.BASS_ChannelSeconds2Bytes.argtypes = [c_uint32, c_double]

        b.BASS_ErrorGetCode.restype = c_int

        b.BASS_SetDevice.restype  = c_bool
        b.BASS_SetDevice.argtypes = [c_uint32]

        b.BASS_GetDeviceInfo.restype  = c_bool
        b.BASS_GetDeviceInfo.argtypes = [c_uint32, ctypes.POINTER(BASS_DEVICEINFO)]

        b.BASS_SetConfig.restype  = c_bool
        b.BASS_SetConfig.argtypes = [c_uint32, c_uint32]

        b.BASS_ChannelGetAttribute.restype  = c_bool
        b.BASS_ChannelGetAttribute.argtypes = [c_uint32, c_uint32, ctypes.POINTER(c_float)]

        # Vúmetros: BASS_ChannelGetLevelEx (BASS 2.4.15+); si la DLL es más
        # vieja se usa BASS_ChannelGetLevel y se aplica el volumen a mano.
        try:
            b.BASS_ChannelGetLevelEx.restype  = c_bool
            b.BASS_ChannelGetLevelEx.argtypes = [c_uint32, ctypes.POINTER(c_float), c_float, c_uint32]
            self._level_ex = True
        except AttributeError:
            self._level_ex = False
        b.BASS_ChannelGetLevel.restype  = c_uint32
        b.BASS_ChannelGetLevel.argtypes = [c_uint32]
        self.ultimo_error = 0

        self._handles: dict[int, int] = {}  # canal_idx -> BASS handle
        self._inited:  set[int] = set()     # tarjetas inicializadas (indice real)

    def init(self, device: int = -1):
        """
        Inicializa BASS en la tarjeta del AIRE (debe funcionar o el motor no
        arranca). -1 = predeterminada del sistema, 0 = "sin sonido" (los
        streams corren en tiempo real pero no salen por ninguna tarjeta).
        """
        self._bass.BASS_SetConfig(BASS_CONFIG_UNICODE, 1)
        real = self.ensure_device(device)
        log.info("[BASS] Motor inicializado -- 44100 Hz, dispositivo %d", real)

    def list_devices(self) -> list[dict]:
        """Tarjetas de sonido que ve BASS (indice 0 = sin sonido)."""
        out, info = [], BASS_DEVICEINFO()
        for i in range(64):
            if not self._bass.BASS_GetDeviceInfo(i, ctypes.byref(info)):
                break
            out.append({
                "index":           i,
                "nombre":          _decode_name(info.name) or f"Dispositivo {i}",
                "habilitado":      bool(info.flags & BASS_DEVICE_ENABLED),
                "predeterminado":  bool(info.flags & BASS_DEVICE_DEFAULT),
            })
        return out

    def _resolve(self, device: int) -> int:
        """-1 -> indice real de la tarjeta predeterminada del sistema."""
        if device != -1:
            return device
        for d in self.list_devices():
            if d["predeterminado"] and d["index"] > 0:
                return d["index"]
        return 1

    def ensure_device(self, device: int) -> int:
        """Inicializa la tarjeta si hace falta. Devuelve su indice real."""
        real = self._resolve(int(device))
        if real in self._inited:
            return real
        if not self._bass.BASS_Init(real, 44100, 0, None, None):
            err = self._bass.BASS_ErrorGetCode()
            if err != BASS_ERROR_ALREADY:
                raise RuntimeError(
                    f"BASS_Init fallo (codigo de error: {err}, dispositivo {real}). "
                    f"Verifica que la tarjeta de audio exista y no este ocupada."
                )
        self._inited.add(real)
        return real

    def load_stream(self, path: str, channel_idx: int, device: int = -1) -> bool:
        """
        Crea un stream BASS desde un archivo de audio en disco, en la
        tarjeta `device`. Libera el stream anterior en ese canal si existe.
        """
        self._free_channel(channel_idx)
        try:
            real = self.ensure_device(device)
        except RuntimeError as exc:
            log.error("[BASS] %s", exc)
            return False
        self._bass.BASS_SetDevice(real)

        flags = BASS_STREAM_PRESCAN | BASS_SAMPLE_FLOAT
        if self._windows:
            handle = self._bass.BASS_StreamCreateFile(
                False, str(path), 0, 0, flags | BASS_UNICODE
            )
        else:
            handle = self._bass.BASS_StreamCreateFile(
                False, str(path).encode("utf-8"), 0, 0, flags
            )

        if not handle:
            err = self._bass.BASS_ErrorGetCode()
            log.error("[BASS] BASS_StreamCreateFile fallo: '%s' (err=%d)", path, err)
            return False

        self._handles[channel_idx] = handle
        return True

    def play(self, channel_idx: int, restart: bool = False) -> bool:
        handle = self._handles.get(channel_idx)
        if not handle:
            return False
        return bool(self._bass.BASS_ChannelPlay(handle, restart))

    def stop(self, channel_idx: int):
        handle = self._handles.get(channel_idx)
        if handle:
            self._bass.BASS_ChannelStop(handle)
        self._free_channel(channel_idx)

    def pause(self, channel_idx: int) -> bool:
        """Congela el canal donde está (BASS_ChannelPlay sin restart lo reanuda)."""
        handle = self._handles.get(channel_idx)
        return bool(handle and self._bass.BASS_ChannelPause(handle))

    def set_volume(self, channel_idx: int, vol: float):
        handle = self._handles.get(channel_idx)
        if handle:
            self._bass.BASS_ChannelSetAttribute(
                handle, BASS_ATTRIB_VOL, c_float(max(0.0, min(VOL_MAX, vol)))
            )

    def slide_volume(self, channel_idx: int, vol: float, ms: int):
        """Rampa de volumen dentro del mixer de BASS (no bloquea)."""
        handle = self._handles.get(channel_idx)
        if handle:
            self._bass.BASS_ChannelSlideAttribute(
                handle, BASS_ATTRIB_VOL, c_float(max(0.0, min(VOL_MAX, vol))), max(0, int(ms))
            )

    def is_playing(self, channel_idx: int) -> bool:
        handle = self._handles.get(channel_idx)
        if not handle:
            return False
        return self._bass.BASS_ChannelIsActive(handle) == BASS_ACTIVE_PLAYING

    def is_active(self, channel_idx: int) -> bool:
        handle = self._handles.get(channel_idx)
        if not handle:
            return False
        state = self._bass.BASS_ChannelIsActive(handle)
        return state in (BASS_ACTIVE_PLAYING, BASS_ACTIVE_PAUSED, BASS_ACTIVE_STALLED)

    def get_position_sec(self, channel_idx: int) -> float:
        handle = self._handles.get(channel_idx)
        if not handle:
            return 0.0
        pos_bytes = self._bass.BASS_ChannelGetPosition(handle, BASS_POS_BYTE)
        if pos_bytes < 0:
            return 0.0
        return float(self._bass.BASS_ChannelBytes2Seconds(handle, pos_bytes))

    def set_position_sec(self, channel_idx: int, sec: float) -> bool:
        handle = self._handles.get(channel_idx)
        if not handle:
            return False
        pos_bytes = self._bass.BASS_ChannelSeconds2Bytes(handle, c_double(sec))
        if pos_bytes < 0 or not self._bass.BASS_ChannelSetPosition(handle, pos_bytes, BASS_POS_BYTE):
            self.ultimo_error = self._bass.BASS_ErrorGetCode()
            log.error("[BASS] No se pudo mover a %.1fs (canal %d, error %d)", sec, channel_idx, self.ultimo_error)
            return False
        return True

    def nivel(self, channel_idx: int) -> tuple[float, float]:
        """Pico (0..1) izquierdo y derecho de lo que suena ahora en el canal."""
        handle = self._handles.get(channel_idx)
        if not handle or self._bass.BASS_ChannelIsActive(handle) != BASS_ACTIVE_PLAYING:
            return 0.0, 0.0
        if self._level_ex:
            lv = (c_float * 2)()
            if self._bass.BASS_ChannelGetLevelEx(handle, lv, c_float(0.05),
                                                 BASS_LEVEL_STEREO | BASS_LEVEL_VOLPAN):
                return float(lv[0]), float(lv[1])
            return 0.0, 0.0
        raw = self._bass.BASS_ChannelGetLevel(handle)
        if raw == 0xFFFFFFFF:
            return 0.0, 0.0
        vol = c_float(1.0)
        self._bass.BASS_ChannelGetAttribute(handle, BASS_ATTRIB_VOL, ctypes.byref(vol))
        return (raw & 0xFFFF) / 32768 * vol.value, (raw >> 16) / 32768 * vol.value

    def get_duration_sec(self, channel_idx: int) -> float:
        handle = self._handles.get(channel_idx)
        if not handle:
            return 0.0
        len_bytes = self._bass.BASS_ChannelGetLength(handle, BASS_POS_BYTE)
        if len_bytes < 0:
            return 0.0
        return float(self._bass.BASS_ChannelBytes2Seconds(handle, len_bytes))

    def _free_channel(self, channel_idx: int):
        handle = self._handles.pop(channel_idx, None)
        if handle:
            self._bass.BASS_StreamFree(handle)

    def teardown(self):
        for idx in list(self._handles.keys()):
            self._free_channel(idx)
        for real in self._inited:
            if self._bass.BASS_SetDevice(real):
                self._bass.BASS_Free()
        self._inited.clear()
        log.info("[BASS] Motor liberado.")


# ══════════════════════════════════════════════════════════════════════════════
# Mock Backend -- Solo para desarrollo sin hardware de audio
# ══════════════════════════════════════════════════════════════════════════════
class MockBackend:
    """
    Simula los canales con el reloj de pared: la posicion avanza en tiempo
    real y el canal 'termina' al llegar a la duracion real del archivo
    (leida con mutagen; 10 s si no se puede leer). No emite sonido.
    """

    DEVICES = [
        {"index": 0, "nombre": "Sin sonido", "habilitado": True, "predeterminado": False},
        {"index": 1, "nombre": "Simulada 1", "habilitado": True, "predeterminado": True},
        {"index": 2, "nombre": "Simulada 2", "habilitado": True, "predeterminado": False},
    ]

    def __init__(self):
        self._ch: dict[int, dict] = {}

    def init(self, device: int = -1):
        self.ensure_device(device)

    def list_devices(self) -> list[dict]:
        return [dict(d) for d in self.DEVICES]

    def ensure_device(self, device: int) -> int:
        real = 1 if int(device) == -1 else int(device)
        if not any(d["index"] == real for d in self.DEVICES):
            raise RuntimeError(f"Dispositivo {real} inexistente")
        return real

    def device_of(self, channel_idx: int) -> Optional[int]:
        ch = self._ch.get(channel_idx)
        return ch["device"] if ch else None

    @staticmethod
    def _probe(path: str) -> float:
        try:
            from mutagen import File as MutagenFile
            info = MutagenFile(path)
            if info is not None and info.info.length > 0:
                return float(info.info.length)
        except Exception:
            pass
        return 10.0

    @staticmethod
    def _mudo(path: str) -> bool:
        """Un WAV que es todo silencio (sirve para probar el detector de aire muerto)."""
        if not str(path).lower().endswith(".wav"):
            return False
        try:
            import wave
            with wave.open(str(path), "rb") as w:
                datos = w.readframes(w.getframerate() * 30)
            return not any(datos)
        except Exception:
            return False

    def load_stream(self, path: str, channel_idx: int, device: int = -1) -> bool:
        try:
            real = self.ensure_device(device)
        except RuntimeError:
            return False
        self._ch[channel_idx] = {"dur": self._probe(path), "started": None,
                                 "offset": 0.0, "device": real, "mudo": self._mudo(path)}
        return True

    def play(self, channel_idx: int, restart: bool = False) -> bool:
        ch = self._ch.get(channel_idx)
        if not ch:
            return False
        if ch["started"] is None or restart:
            ch["started"] = time.monotonic()
        log.info("[Mock] Canal %d activo por %.1fs", channel_idx, ch["dur"] - ch["offset"])
        return True

    def stop(self, channel_idx: int):
        self._ch.pop(channel_idx, None)

    def pause(self, channel_idx: int) -> bool:
        """Guarda la posición y deja de avanzar; play() continúa desde ahí."""
        ch = self._ch.get(channel_idx)
        if not ch or ch["started"] is None:
            return False
        ch["offset"] = self.get_position_sec(channel_idx)
        ch["started"] = None
        return True

    def set_volume(self, channel_idx: int, vol: float): pass
    def slide_volume(self, channel_idx: int, vol: float, ms: int): pass

    def get_position_sec(self, channel_idx: int) -> float:
        ch = self._ch.get(channel_idx)
        if not ch:
            return 0.0
        if ch["started"] is None:              # sin arrancar o en pausa: donde quedó
            return ch["offset"]
        # el paréntesis importa: (offset + ahora) - inicio pierde precisión y da 5.79999 en vez de 5.8
        return min(ch["dur"], ch["offset"] + (time.monotonic() - ch["started"]))

    def set_position_sec(self, channel_idx: int, sec: float) -> bool:
        ch = self._ch.get(channel_idx)
        if not ch:
            return False
        ch["offset"] = max(0.0, min(float(sec), ch["dur"]))
        if ch["started"] is not None:
            ch["started"] = time.monotonic()     # sonando: la posición parte de aquí
        return True

    def is_playing(self, channel_idx: int) -> bool:
        ch = self._ch.get(channel_idx)
        return bool(ch and ch["started"] is not None
                    and self.get_position_sec(channel_idx) < ch["dur"])

    def is_active(self, channel_idx: int) -> bool:
        return self.is_playing(channel_idx)

    def nivel(self, channel_idx: int) -> tuple[float, float]:
        """Simulado: nivel que se mueve como música mientras el canal suena."""
        if not self.is_playing(channel_idx) or self._ch[channel_idx].get("mudo"):
            return 0.0, 0.0
        base = 0.45 + 0.25 * abs(((time.monotonic() * 1.7) % 2) - 1)
        return min(1.0, base + random.uniform(-0.1, 0.1)), min(1.0, base + random.uniform(-0.1, 0.1))

    def get_duration_sec(self, channel_idx: int) -> float:
        ch = self._ch.get(channel_idx)
        return ch["dur"] if ch else 0.0

    def teardown(self): pass


# ══════════════════════════════════════════════════════════════════════════════
# Watchdog -- Guardian de reproduccion 24/7
# ══════════════════════════════════════════════════════════════════════════════
class Watchdog:
    """
    Hilo guardian que verifica periodicamente si el motor sigue activo y
    avanzando. Detecta DOS tipos de fallo distintos:

    FALLO 1 — SILENCIO TOTAL (canal inactivo):
        BASS_ChannelIsActive devuelve STOPPED cuando deberia devolver PLAYING.
        Esto ocurre por un error irrecuperable del codec o del dispositivo.
        Accion: emite 'watchdog_recovery' → el motor intenta reanudar o salta.

    FALLO 2 — STALL DE POSICION (canal activo pero congelado):
        BASS reporta PLAYING, pero la posicion en segundos no avanza durante
        STALL_TOLERANCE segundos. Esto ocurre en algunos casos de corrupcion
        de buffer o cuando el driver de audio entra en un estado inconsistente.
        Accion: emite 'watchdog_stall' → el motor reinicia el stream desde cero.

    La distincion entre ambos fallos es importante: un stall puede aparecer
    aunque BASS_ChannelIsActive sea PLAYING, por eso no basta con verificar
    si el canal esta "activo". Hay que verificar que la posicion AVANCE.

    Principio de diseno:
        El Watchdog solo detecta y emite eventos. Nunca actua directamente
        sobre el audio. La decision de que hacer la toma el motor principal.
        Esto garantiza que el Watchdog no pueda crear race conditions.
    """

    CHECK_INTERVAL    = 2.0   # segundos entre verificaciones
    SILENCE_TOLERANCE = 4.0   # segundos de canal inactivo antes de actuar
    STALL_TOLERANCE   = 5.0   # segundos sin avance de posicion antes de actuar
    MIN_POSITION_DELTA = 0.5  # avance minimo esperado en segundos por ciclo

    def __init__(self, bus: EventBus, get_state: Callable):
        self.bus       = bus
        self.get_state = get_state  # funcion → dict con estado del motor
        self._thread:           Optional[threading.Thread] = None
        self._running           = False
        self._silence_since:    Optional[float] = None
        self._last_position:    Optional[float] = None  # posicion en segundos
        self._stall_since:      Optional[float] = None  # inicio del stall de posicion

    def start(self):
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, name="AudioWatchdog", daemon=True
        )
        self._thread.start()
        log.info(
            "[Watchdog] Iniciado -- CHECK=%.0fs SILENCE=%.0fs STALL=%.0fs",
            self.CHECK_INTERVAL, self.SILENCE_TOLERANCE, self.STALL_TOLERANCE
        )

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._check()
            except Exception as exc:
                log.error("[Watchdog] Error inesperado: %s", exc)
            time.sleep(self.CHECK_INTERVAL)

    def _check(self):
        state = self.get_state()

        # Si el motor no deberia estar sonando, reiniciar todos los contadores.
        if not state.get("should_be_playing"):
            self._silence_since = None
            self._last_position = None
            self._stall_since   = None
            return

        is_playing       = state.get("is_playing", False)
        current_position = state.get("current_position_sec", None)

        # ── FALLO 1: Canal silencioso ────────────────────────────────────────
        if not is_playing:
            self._last_position = None
            self._stall_since   = None

            if self._silence_since is None:
                self._silence_since = time.monotonic()
                log.warning("[Watchdog] Silencio detectado -- esperando %.0fs...",
                            self.SILENCE_TOLERANCE)
                return

            elapsed = time.monotonic() - self._silence_since
            if elapsed >= self.SILENCE_TOLERANCE:
                log.error("[Watchdog] Silencio prolongado (%.1fs) -- recuperando.", elapsed)
                self._silence_since = None
                self.bus.emit("watchdog_recovery", {
                    "tipo":         "silence",
                    "silencio_seg": elapsed,
                    "current_track": state.get("current_track"),
                })
            return

        # ── Canal activo: reiniciar contador de silencio ─────────────────────
        self._silence_since = None

        # ── FALLO 2: Stall de posicion (canal PLAYING pero posicion congelada) ─
        if current_position is None:
            # El backend (mock o pygame basico) no reporta posicion → ignorar
            self._last_position = None
            self._stall_since   = None
            return

        if self._last_position is None:
            # Primera vez que vemos la posicion, solo guardar como referencia
            self._last_position = current_position
            self._stall_since   = None
            return

        delta = current_position - self._last_position
        self._last_position = current_position  # actualizar para el proximo ciclo

        if delta >= self.MIN_POSITION_DELTA or delta < 0:
            # Posicion avanzando con normalidad (o retrocedio porque cambio la
            # pista entre dos verificaciones) → reiniciar contador de stall
            self._stall_since = None
            return

        # La posicion no avanzo lo suficiente
        if self._stall_since is None:
            self._stall_since = time.monotonic()
            log.warning(
                "[Watchdog] Posicion sin avanzar (delta=%.3fs) -- esperando %.0fs...",
                delta, self.STALL_TOLERANCE
            )
            return

        stall_elapsed = time.monotonic() - self._stall_since
        if stall_elapsed >= self.STALL_TOLERANCE:
            log.error(
                "[Watchdog] STALL confirmado: %.1fs sin avance de posicion. Reiniciando stream.",
                stall_elapsed
            )
            self._stall_since   = None
            self._last_position = None
            self.bus.emit("watchdog_stall", {
                "tipo":           "position_stall",
                "stall_seg":      stall_elapsed,
                "last_position":  current_position,
                "current_track":  state.get("current_track"),
            })


# ══════════════════════════════════════════════════════════════════════════════
# Motor Principal
# ══════════════════════════════════════════════════════════════════════════════
CANAL_A        = 0
CANAL_B        = 1
CANAL_EFX_BASE = 2  # canales 2-7 para cartuchera (6 slots disponibles)
CANAL_CUE      = 8  # preescucha (sale por la tarjeta de preescucha, nunca al aire)
CANAL_TIP      = 9  # tip montado sobre el intro o el puente de las canciones (al aire)

# Salidas de audio (roles). Cada una apunta a una tarjeta de la PC
# (config dispositivo_<rol>; -1 = predeterminada del sistema).
ROLES_SALIDA = ("aire", "preescucha", "cartuchera")
IGUAL_QUE_AIRE = -2   # valor de dispositivo_cartuchera: misma tarjeta que el aire


class AudioEngine:
    """
    Motor central de Onix FM.

    Lifecycle:
        engine = AudioEngine()
        engine.start()
        # ... operacion 24/7 ...
        engine.stop()

    API publica (segura desde cualquier hilo, nunca bloquea):
        play_track, play_next, queue_track, clear_queue, skip, stop_playback,
        fire_effect, set_mode, lanzar_tanda, cue_play, cue_stop,
        list_devices, status

    MODO AUTOMATICO (estilo Jazler SoHo):
        El planificador (planner.py) arma las proximas `horas_planificadas`
        siguiendo los relojes de cada hora, con las tandas como marcadores.
        El motor consume ese plan en orden; el operador lo ve en el Studio y
        puede quitar o mover items. En manual el plan tambien se ve, pero
        solo avanza cuando el operador pide "siguiente".

    PAUTAS SEGUN EL MODO:
        automatico → las tandas salen solas en el siguiente cambio de pista.
        manual     → las tandas quedan EN ESPERA y el operador las lanza
                     (lanzar_tanda). Si no las lanza en su hora, se pierden
                     y quedan registradas como no emitidas (pauta_missed).
    """

    TICK               = 0.05   # segundos entre ciclos del loop
    PAUTAS_INTERVAL    = 5.0    # segundos entre consultas a la tabla pautas
    IDLE_RETRY         = 10.0   # espera antes de reintentar si no hay nada que sonar
    FAILED_TTL         = 600.0  # segundos que se evita un audio cuyo archivo fallo
    SPOT_FADE_IN       = 1.0    # salida de la musica al entrar una cuña
    SPOT_FADE_OUT      = 0.2    # empalme al terminar una cuña
    ENTRADA_CORTO      = 0.05   # ataque de una cuña / jingle / sweeper / tip (entra a pleno volumen)
    FADE_CORTE         = 5.0    # fade de la canción cuando una tanda o evento INMEDIATO la corta (config fade_corte_seg)
    VOZ_DUCK           = 0.2    # la música bajo la voz queda al 20 % (≈ -14 dB); se cambia con config voz_fondo_pct
    VOZ_BAJAR_MS       = 400    # cuánto tarda en bajar la música cuando entra la voz
    VOZ_SALIDA_CAMA    = 1.5    # la música de fondo se va en este tiempo antes de que termine la voz
    VOZ_FIN            = 0.2    # la siguiente entra este tiempo antes del final de la voz
    TIP_CORTE          = 0.8    # fundido del tip que no cabe en el intro: termina justo donde entra la voz
    TIP_SALIDA         = 0.3    # fundido del tip cuando lo interrumpe una cuña, una voz o el operador
    TIP_SUBIR_MS       = 600    # la música vuelve a pleno si el tip se corta de golpe
    TIP_DUCK           = 0.45   # la música bajo un tip queda al 45 % (≈ -7 dB, se oye el fondo); config tip_fondo_pct
    ENTRADA_CANCION    = 0.5    # la canción que entra en un crossfade llega a su volumen normal en este tiempo (si no tiene fade_in propio); la que sale sí baja en todo el crossfade
    TIP_XFADE          = 1.5    # en un puente el cambio de canción es corto: la siguiente se oye en la 2.ª mitad del tip (con 4 s de crossfade solo se oiría el tip)
    TIP_BAJAR_MS       = 250    # bajada rápida al entrar el tip (que no se pise su primera palabra)
    TIP_SUBE_ANTES     = 1.3    # la música empieza a subir este tiempo ANTES de que acabe el tip: llega a pleno justo al terminar

    def __init__(self, db_path: Path = None, respaldo_dir: Path = None):
        self.bus = EventBus()

        self._audio = BASSBackend() if BACKEND == "bass" else MockBackend()

        self._db       = db_path or DB_PATH
        self._respaldo_dir = respaldo_dir or RESPALDO_DIR
        self._now      = datetime.now          # reloj de pared (las pruebas lo sustituyen)
        self._planner  = Planner(self._db)
        self._watchdog = Watchdog(self.bus, self._watchdog_state)

        self._mode             = "manual"
        self._active_canal     = CANAL_A
        self._current_track:   Optional[dict] = None
        self._current_item:    Optional[dict] = None
        self._playlist_queue:  deque = deque()   # resumenes {id, titulo, artista}
        self._priority_queue:  deque = deque()   # pautas pendientes de esta hora
        self._recent_history:  deque = deque(maxlen=50)
        self._failed_ids:      dict[int, float] = {}

        self._cmds: "queue.Queue[tuple]" = queue.Queue()

        self._xfade:           Optional[dict] = None  # {canal, until, track}
        # Voice track sobre música: la canción anterior sigue sonando bajita en su canal
        self._cama:            Optional[dict] = None  # {canal, track, item, saliendo}
        self._outro_voz:       Optional[float] = None # fin de la voz de la canción: si viene un voice track, entra ahí
        self._crossfade_secs   = 4.0
        self._master_volume    = 1.0
        self._effect_ducking   = False
        self._ducking_vol      = 0.25
        self._norm_activa      = True
        self._norm_lufs        = -16.0
        self._duck_until       = 0.0
        self._voz_duck         = self.VOZ_DUCK   # volumen de la música bajo una locución (0–1)
        self._tip_duck         = self.TIP_DUCK   # volumen de la música bajo un tip (0–1)
        self._efx: dict[int, dict] = {}        # canal de cartuchera → {boton, audio_id, titulo, fin, hasta}
        # Tips sobre la música (ver calcular_tip): uno a la vez, en CANAL_TIP
        self._tip:      Optional[dict] = None  # programado o sonando sobre la canción `item`
        self._tip_pend: Optional[dict] = None  # tip de intro esperando a la próxima canción

        # Salidas: rol -> dispositivo BASS (-1 = predeterminado del sistema)
        self._devices: dict[str, int] = {rol: -1 for rol in ROLES_SALIDA}
        self._cue_track:       Optional[dict] = None

        # Modo manual: el operador libera la tanda pendiente
        self._tanda_liberada   = False

        # "Detener al terminar" (stop-next de Jazler): la pista actual suena
        # completa, no entra nada despues y la automatizacion queda RETENIDA
        # hasta que el operador pida reproducir (para hablar en vivo).
        self._detener_al_terminar = False
        self._retenido            = False
        self._pausado             = False      # PAUSA del operador (lo que suena quedó congelado)
        self._pausado_desde: Optional[float] = None
        self._pausa_avisada       = False

        # Programa: lo que sonara las proximas horas (ver planner.py).
        # Solo el hilo del motor lo modifica; el lock protege las lecturas de la API.
        self._programa:        deque = deque()
        self._programa_lock    = threading.Lock()
        self._horas_plan       = 2.0
        self._last_plan_check  = 0.0
        self._replanificar     = True

        self._duration         = 0.0
        self._mix_point        = float("inf")
        self._hard_end         = float("inf")   # corte duro (cue_outro de la pauta)
        self._mix_fired        = False

        # Tandas de la hora en curso: {minuto: [pauta_id, ...]}
        self._tandas_minutos:  list[int] = [15, 45]
        self._tandas_cierre:   list[int] = []   # tandas que TERMINAN en su minuto
        self._plan_slot:       Optional[str] = None
        self._cierres_hechos:  set[tuple] = set()    # (slot, minuto) de tandas «al cierre» ya lanzadas (sobreviven al cambio de hora)
        self._plan:            dict[int, list[int]] = {}
        self._tandas_lanzadas: set[int] = set()
        self._tandas_inmediatas: list[int] = []      # minutos cuya tanda corta la cancion
        # Valores generales y grilla por día y hora: la hora en curso toma de ahí
        # sus minutos, inmediatas y cierre (ver _tandas_de_ahora)
        self._tandas_base: tuple = ([15, 45], [], [])
        self._tandas_grilla: dict = {}
        self._inmediatas_hechas: set[tuple] = set()  # (slot, minuto) ya cortadas
        self._dur_cierre: dict[int, float] = {}       # minuto al cierre -> segundos de su bloque (hora en curso)
        self._fade_corte: float = self.FADE_CORTE     # config fade_corte_seg
        self._fade_curva: list[float] = list(CURVA_FADE_DEFECTO)   # config fade_corte_curva (multiplicadores 0–1)
        self._corte_fade: Optional[float] = None      # fade a usar en el próximo arranque (corte inmediato en curso)
        # Corte inmediato en dos tiempos: la canción BAJA los segundos previos y la cuña entra a la hora exacta
        self._corte_pend: Optional[dict] = None       # {clave, minuto, target, canal, vol}
        self._prefade: Optional[dict] = None          # {canal, vol} mientras la canción baja antes del corte
        self._evento_prefade: set = set()             # eventos inmediatos cuya canción ya empezó a bajar
        self._eventos_emitidos: set[str] = set()     # evento_key de eventos a hora fija ya al aire
        # Detector de aire muerto (ver _vigilar_silencio)
        self._silencio_seg: float = 10.0
        self._silencio_desde: Optional[float] = None
        self._silencio_avisado = None                 # pista (o "vacio") ya avisada
        self._silencios: deque = deque(maxlen=10)     # momentos de los últimos saltos
        self._tanda_max_seg    = 240
        self._pautas_fallidas: set[tuple[str, int]] = set()   # (slot, pauta_id)
        self._respaldo_recent: deque = deque(maxlen=20)

        self._running            = False
        self._thread:            Optional[threading.Thread] = None
        self._should_be_playing  = False
        self._last_pautas_check  = 0.0
        self._idle_retry_at      = 0.0
        self.last_error:         Optional[str] = None

        # Cola de movimientos fisicos de archivos diferidos hasta fin de pista
        self._deferred_moves: list[dict] = []

        log.info("[Engine] Backend seleccionado: %s", BACKEND)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._load_config()
        self._audio.init(self._devices["aire"])
        self._running = True

        self._thread = threading.Thread(
            target=self._run_loop, name="AudioEngineLoop", daemon=True
        )
        self._thread.start()

        self.bus.subscribe("watchdog_recovery", self._on_watchdog_recovery)
        self.bus.subscribe("watchdog_stall",    self._on_watchdog_stall)
        self._watchdog.start()

        log.info("[Engine] Motor de audio iniciado (BD: %s).", self._db)

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._watchdog.stop()
        if self._thread:
            self._thread.join(timeout=5)
        self._audio.teardown()
        self._current_track     = None
        self._should_be_playing = False
        self.bus.emit("engine_stopped", {})
        log.info("[Engine] Motor detenido.")

    def reload_config(self):
        """Relee la tabla config (la API lo llama al guardar la configuracion)."""
        modo_actual = self._mode
        self._load_config()
        self._mode = modo_actual   # el modo solo se cambia con set_mode()
        # Rehacer el reparto de la hora: un ajuste a mano que se deshace vuelve
        # al automático (lo ya emitido no se repite: se mira el historial)
        self._plan_slot = None

    def _load_config(self):
        """Lee la tabla `config` UNA vez (antes abría una conexión a MariaDB por cada ajuste: ~1 s)."""
        try:
            conn = _db_conn(self._db)
            try:
                self._cfg_snap = {r[0]: r[1] for r in conn.execute("SELECT clave, valor FROM config").fetchall()}
            finally:
                conn.close()
        except Exception:
            self._cfg_snap = None
        try:
            self._load_config_inner()
        finally:
            self._cfg_snap = None

    def _load_config_inner(self):
        try:
            self._crossfade_secs = max(0.0, float(self._cfg("crossfade_seg", "4")))
            modo = self._cfg("modo", "manual")
            self._mode = modo if modo in ("manual", "automatico") else "manual"
            self._ducking_vol = 10 ** (float(self._cfg("ducking_db", "-12")) / 20)
            # Música bajo la voz del locutor / voice track, en % (Admin › Motor de audio)
            self._voz_duck = max(0.0, min(float(self._cfg("voz_fondo_pct", str(int(self.VOZ_DUCK * 100)))), 100.0)) / 100
            self._tip_duck = max(0.0, min(float(self._cfg("tip_fondo_pct", str(int(self.TIP_DUCK * 100)))), 100.0)) / 100
            self._fade_corte = max(0.2, min(float(self._cfg("fade_corte_seg", str(self.FADE_CORTE)) or self.FADE_CORTE), 15.0))
            self._fade_curva = parse_curva_fade(self._cfg("fade_corte_curva", ""))
            fijar_regla_voces(self._cfg("regla_voces_mujer", "1") not in ("0", "false", ""))
            try:
                _c = _db_conn(self._db)
                try:
                    fijar_conflictos_rubros(leer_conflictos_rubros(_c))
                finally:
                    _c.close()
            except Exception:
                pass
            self._norm_activa = self._cfg("normalizacion_activa", "1") not in ("0", "false", "")
            self._norm_lufs   = float(self._cfg("normalizacion_lufs", "-16"))
            for rol in ROLES_SALIDA:
                defecto = str(IGUAL_QUE_AIRE) if rol == "cartuchera" else "-1"
                self._devices[rol] = int(float(self._cfg(f"dispositivo_{rol}", defecto)))
            self._horas_plan = max(0.5, min(24.0, float(self._cfg("horas_planificadas", "2"))))
            self._silencio_seg = max(0.0, float(self._cfg("silencio_seg", "10") or 0))
            self._replanificar = True
            # Compatibilidad: ONIX_AUDIO_DEVICE fuerza la tarjeta del aire
            if os.getenv("ONIX_AUDIO_DEVICE") not in (None, ""):
                self._devices["aire"] = int(os.environ["ONIX_AUDIO_DEVICE"])
            cie = self._cfg("tandas_cierre", "")
            self._tandas_cierre = parse_minutos(cie) if cie.strip() else []
            inm = self._cfg("tandas_inmediatas", "")
            self._tandas_inmediatas = parse_minutos(inm) if inm.strip() else []
            self._tanda_max_seg = int(float(self._cfg("tanda_max_seg", "240") or 0))
            minutos = parse_minutos(self._cfg("tandas_minutos", "15,45"))
            self._tandas_base = (minutos, list(self._tandas_inmediatas), list(self._tandas_cierre))
            self._tandas_grilla = cargar_grilla(self._cfg("tandas_grilla", ""))
            if minutos != self._tandas_minutos:
                self._tandas_minutos = minutos
                self._plan_slot = None   # replanificar la hora con las nuevas tandas
            self._tandas_de_ahora()
        except Exception as exc:
            log.warning("[Engine] Config no cargada: %s", exc)

    # ── Loop principal ────────────────────────────────────────────────────────

    def _run_loop(self):
        while self._running:
            try:
                self._drain_commands()
                self._finish_crossfade()
                self._vigilar_efectos()
                self._restore_ducking()
                self._check_pautas()
                self._check_tanda_inmediata()
                self._check_evento_inmediato()
                self._actualizar_programa()
                self._tick()
                self._process_deferred_moves()
                self._emitir_niveles()
            except Exception as exc:
                log.error("[Engine] Error en loop: %s", exc, exc_info=True)
            time.sleep(self.TICK)

    VU_INTERVAL = 0.1     # 10 lecturas por segundo para los vúmetros

    def _emitir_niveles(self):
        """Nivel de la salida del AIRE (canales A/B y cartuchera; no la preescucha)."""
        ahora = time.monotonic()
        if ahora - getattr(self, "_ultimo_vu", 0.0) < self.VU_INTERVAL:
            return
        self._ultimo_vu = ahora
        izq = der = 0.0
        for canal in (CANAL_A, CANAL_B, *range(CANAL_EFX_BASE, CANAL_EFX_BASE + 6)):
            l, r = self._audio.nivel(canal)
            izq, der = max(izq, l), max(der, r)
        if izq or der or getattr(self, "_vu_previo", (0.0, 0.0)) != (0.0, 0.0):
            self._vu_previo = (izq, der)
            self.bus.emit("vu", {"l": round(izq, 3), "r": round(der, 3)})
        self._vigilar_silencio(max(izq, der), ahora)

    # ── Detector de aire muerto ───────────────────────────────────────────────

    SILENCIO_UMBRAL = 0.003      # ≈ -50 dBFS: por debajo, el aire está en silencio
    SILENCIO_RAFAGA = 3          # saltos seguidos en 10 min = falla del equipo, no del archivo

    def _vigilar_silencio(self, nivel: float, ahora: float):
        """
        Aire muerto: lo que suena lleva `silencio_seg` segundos en silencio
        (archivo dañado, silencio al final de un tema, canal trabado), o en
        automático no hay nada sonando y nadie lo pidió (DETENER AL TERMINAR).

        En automático pasa a lo siguiente; en manual solo avisa (el operador
        está en cabina). Si salta 3 veces en 10 min deja de saltar: lo más
        probable es que falle la tarjeta o el medidor, no los archivos, y
        saltar sin parar empeoraría el aire.
        """
        if not self._silencio_seg or not self._running:
            self._silencio_desde = None
            return
        if self._pausado:
            # Pausa del operador: no es una falla. Si se olvida, avisar (una vez)
            self._silencio_desde = None
            espera = max(self._silencio_seg * 3, 30)
            if not self._pausa_avisada and self._pausado_desde and ahora - self._pausado_desde >= espera:
                self._pausa_avisada = True
                seg = round(ahora - self._pausado_desde)
                log.error("[Engine] El aire lleva %ss en PAUSA", seg)
                self.bus.emit("aire_muerto", {"motivo": "pausa", "accion": "aviso", "segundos": seg,
                                              "msg": f"El aire lleva {seg}s en PAUSA: pulsa REANUDAR"})
            return
        sonando = self._current_track is not None
        vacio_inesperado = (not sonando and self._mode == "automatico" and not self._retenido)
        # En un crossfade suenan dos canales: no hay silencio que vigilar
        if (sonando and nivel >= self.SILENCIO_UMBRAL) or (not sonando and not vacio_inesperado) or self._xfade:
            self._silencio_desde = None
            if sonando and nivel >= self.SILENCIO_UMBRAL:
                self._silencio_avisado = None
            return
        if self._silencio_desde is None:
            self._silencio_desde = ahora
            return
        if ahora - self._silencio_desde < self._silencio_seg:
            return
        # La pista misma (no su id(): Python reutiliza direcciones de objetos liberados)
        clave = self._current_item if sonando else "vacio"
        if self._silencio_avisado is clave:
            return
        self._silencio_avisado = clave
        seg = round(ahora - self._silencio_desde)
        track = self._current_track or {}
        if not sonando:
            log.error("[Engine] AIRE MUERTO: %ss sin nada que reproducir en automático", seg)
            self.bus.emit("aire_muerto", {"motivo": "sin_audio", "accion": "aviso", "segundos": seg,
                                          "msg": "Automático sin nada que reproducir: revisa relojes, biblioteca o la lista de respaldo"})
            return
        recientes = [t for t in self._silencios if ahora - t < 600]
        datos = {"motivo": "silencio", "segundos": seg, "audio_id": track.get("id"),
                 "titulo": track.get("titulo"), "artista": track.get("artista")}
        if len(recientes) >= self.SILENCIO_RAFAGA:
            log.error("[Engine] AIRE MUERTO otra vez (%d en 10 min): no se salta más", len(recientes) + 1)
            self.bus.emit("aire_muerto", {**datos, "accion": "aviso", "msg":
                "Silencio repetido en varios audios: revisa la tarjeta de sonido, los cables o el medidor"})
            return
        if self._mode == "automatico":
            self._silencios.append(ahora)
            # Como un archivo que falla: el programa lo evita durante FAILED_TTL
            if track.get("id") is not None:
                self._failed_ids[track["id"]] = time.monotonic()
                with self._programa_lock:
                    self._programa = deque(i for i in self._programa if i.get("audio_id") != track["id"])
                # Re-planificación dinámica: recalcula tiempos desde el instante real
                # del salto para no desfasar las tandas comerciales.
                self._cmds.put(("plan_recalcular",))
            else:
                self._replanificar = True

            log.error("[Engine] AIRE MUERTO: '%s' lleva %ss en silencio. Se pasa a lo siguiente",
                      track.get("titulo"), seg)
            self.bus.emit("aire_muerto", {**datos, "accion": "siguiente",
                                          "msg": f"«{track.get('titulo')}» llevaba {seg}s en silencio: se pasó a lo siguiente"})
            self._silencio_desde = None
            self._advance()
        else:
            log.error("[Engine] AIRE MUERTO: '%s' lleva %ss en silencio (manual: solo aviso)",
                      track.get("titulo"), seg)
            self.bus.emit("aire_muerto", {**datos, "accion": "aviso",
                                          "msg": f"«{track.get('titulo')}» lleva {seg}s en silencio"})

    def _tick(self):
        """
        Vigila el canal activo: dispara la mezcla al llegar al punto de mezcla
        y releva la pista cuando termina. En modo automatico arranca solo.
        """
        if self._cue_track and not self._audio.is_active(CANAL_CUE):
            self._do_cue_stop()
        if self._tip:
            self._vigilar_tip()

        if self._xfade or self._pausado:
            return

        track = self._current_track
        if self._cama:
            self._vigilar_cama()
        if track:
            if self._audio.is_active(self._active_canal):
                pos = self._audio.get_position_sec(self._active_canal)
                if pos < self._hard_end:
                    if not self._mix_fired and (
                            pos >= self._mix_point
                            or (self._outro_voz is not None and pos >= self._outro_voz
                                and self._siguiente_es_voz())):
                        # Si no hay siguiente, la pista simplemente termina completa.
                        self._mix_fired = True
                        if not self._detener_al_terminar:
                            if not self._advance() and self._cama:
                                self._recuperar_cama()     # manual sin nada después: vuelve la canción
                    return

            # La pista termino sin relevo previo.
            self._audio.stop(self._active_canal)
            self._current_track = None
            self._current_item  = None
            self.bus.emit("track_ended", {"audio_id": track.get("id"), "completada": True})
            if self._detener_al_terminar:
                self._soltar_cama()                # tampoco sigue la música de fondo de una voz
                self._detener_al_terminar = False
                self._retenido = True
                self._should_be_playing = False
                self.bus.emit("playback_stopped", {"reason": "detener_al_terminar"})
                self.bus.emit("detener_al_terminar", {"activo": False, "retenido": True})
                return
            if not self._advance():
                if self._cama and self._recuperar_cama():
                    return
                self._should_be_playing = False
                self.bus.emit("playback_stopped", {"reason": "sin_siguiente"})
            return

        if (self._mode == "automatico" and not self._retenido
                and time.monotonic() >= self._idle_retry_at):
            if not self._advance():
                self._idle_retry_at = time.monotonic() + self.IDLE_RETRY

    # ── Comandos (API publica → loop) ─────────────────────────────────────────

    def play_track(self, audio_id: int) -> bool:
        """Pone una pista al aire ya (con crossfade si hay otra sonando)."""
        track = self._fetch_track(audio_id)
        if not track:
            return False
        self._cmds.put(("play", {"track": track, "origen": "manual"}))
        return True

    def play_next(self):
        """Arranca lo siguiente de la cola (o de la rotacion en modo automatico)."""
        self._cmds.put(("play_next",))

    def _entrada_cola(self, track: dict, nota: str = "") -> dict:
        from backend.audio.planner import nuevo_uid
        return {
            "uid": nuevo_uid(),            # para elegirla, subirla, bajarla o quitarla en el Studio
            "id": track["id"], "titulo": track["titulo"], "artista": track["artista"],
            "tipo_audio": track.get("tipo_audio") or TIPO_MUSICA,
            "duracion_efectiva": duracion_efectiva(track, self._crossfade_secs),
            "nota": (nota or "").strip()[:500],
        }

    def queue_track(self, audio_id: int, nota: str = "", posicion: Optional[int] = None) -> bool:
        """A la cola del operador. `nota` = memo para el locutor (sale en el
        Studio cuando esta canción está al aire). `posicion`: lugar de la cola
        donde entra (el Studio inserta tras la canción elegida); sin él, al final."""
        track = self._fetch_track(audio_id)
        if not track:
            return False
        entrada = self._entrada_cola(track, nota)
        if posicion is None:
            self._playlist_queue.append(entrada)
            self._replanificar = True
            self._emit_queue()
        else:
            self._cmds.put(("cola_insertar", entrada, int(posicion)))     # en el hilo del motor
        return True

    def cola_mover(self, uid: int, posicion: int):
        self._cmds.put(("cola_mover", int(uid), int(posicion)))

    def cola_quitar(self, uid: int):
        self._cmds.put(("cola_quitar", int(uid)))

    def plan_a_cola(self, uid: int, posicion: int = 0):
        """Pasa un audio del programa automático a la cola del operador (suena antes que el programa)."""
        self._cmds.put(("plan_a_cola", int(uid), int(posicion)))

    def _cola_indice(self, uid: int) -> int:
        return next((i for i, q in enumerate(self._playlist_queue) if q.get("uid") == uid), -1)

    def _cola_insertar(self, entrada: dict, posicion: int):
        self._playlist_queue.insert(max(0, min(posicion, len(self._playlist_queue))), entrada)
        self._replanificar = True
        self._emit_queue()

    def _cola_mover(self, uid: int, posicion: int):
        i = self._cola_indice(uid)
        if i < 0:
            return
        entrada = self._playlist_queue[i]
        del self._playlist_queue[i]
        self._playlist_queue.insert(max(0, min(posicion, len(self._playlist_queue))), entrada)
        self._replanificar = True
        self._emit_queue()

    def _cola_quitar(self, uid: int):
        i = self._cola_indice(uid)
        if i >= 0:
            del self._playlist_queue[i]
            self._replanificar = True
            if self._mode == "automatico":
                self._ajustar_hueco_tanda()
            self._emit_queue()

    def _plan_a_cola(self, uid: int, posicion: int):
        with self._programa_lock:
            it = next((x for x in self._programa if x["uid"] == uid and x.get("tipo") == "audio"), None)
            if it is None:
                return
            self._programa = deque(x for x in self._programa if x["uid"] != uid)
        entrada = {"uid": it["uid"], "id": it["audio_id"], "titulo": it["titulo"], "artista": it["artista"],
                   "duracion_efectiva": float(it.get("duracion") or 180), "nota": ""}
        self._cola_insertar(entrada, posicion)
        self._emit_programa()

    def clear_queue(self):
        self._playlist_queue.clear()
        self._emit_queue()

    def skip(self):
        self._cmds.put(("skip",))

    def stop_playback(self):
        self._cmds.put(("stop",))

    def fire_effect(self, audio_id: int, canal_offset: int = 0, opciones: Optional[dict] = None) -> bool:
        """
        Dispara un jingle o cuña en canal paralelo con auto-ducking.
        opciones (botón de la cartuchera): {"boton": "banco/slot", "mark_in", "mark_out", "volumen" (%)}.
        Con "boton", volver a disparar el mismo botón mientras suena lo DETIENE (como Soho).
        """
        track = self._fetch_track(audio_id)
        if not track:
            return False
        self._cmds.put(("effect", track, canal_offset, opciones or {}))
        return True

    def lanzar_tanda(self, ahora: bool = False) -> int:
        """
        Modo manual: el operador libera las cuñas en espera. Con ahora=True
        entran ya (la musica sale con el empalme corto); si no, en el
        proximo cambio de pista. Devuelve cuantas cuñas habia en espera.
        """
        pendientes = len(self._priority_queue)
        if pendientes:
            self._cmds.put(("lanzar_tanda", bool(ahora)))
        return pendientes

    def cue_play(self, audio_id: int, desde: float = 0.0) -> bool:
        """Preescucha: suena por la salida de preescucha, nunca al aire."""
        track = self._fetch_track(audio_id)
        if not track:
            return False
        self._cmds.put(("cue", track, float(desde or 0)))
        return True

    def cue_stop(self):
        self._cmds.put(("cue_stop",))

    def _device_de(self, rol: str) -> int:
        idx = self._devices.get(rol, -1)
        return self._devices["aire"] if idx == IGUAL_QUE_AIRE else idx

    def list_devices(self) -> list[dict]:
        """Tarjetas de sonido de la PC y que rol tiene asignado cada una."""
        devs = self._audio.list_devices()
        for d in devs:
            d["roles"] = [rol for rol in self._devices
                          if (idx := self._device_de(rol)) == d["index"]
                          or (idx == -1 and d["predeterminado"])]
        return devs

    def set_mode(self, mode: str):
        if mode not in ("manual", "automatico"):
            raise ValueError(f"Modo invalido: {mode!r}")
        anterior, self._mode = self._mode, mode
        self._idle_retry_at = 0.0
        self._retenido = False
        if mode == "manual" and anterior != "manual":
            # Como Jazler: al pasar a manual lo cargado por la automatización se borra
            # (la cola del operador se queda); el operador arma su propio programa.
            self._vaciar_programa()
            self._emit_programa()
            if self._tip_pend and self._tip_pend.get("origen") == "auto":
                self._tip_pend = None
        elif mode == "automatico":
            self._replanificar = True          # al volver, el plan se rehace desde ahora
        self._update_cfg_db("modo", mode)
        self.bus.emit("mode_changed", {"modo": mode, "mode": mode})

    def _drain_commands(self):
        while True:
            try:
                cmd = self._cmds.get_nowait()
            except queue.Empty:
                return
            name = cmd[0]
            if name in ("play", "play_next", "skip", "lanzar_tanda"):
                self._retenido = False          # el operador retoma la emision
            if name == "play":
                self._start(cmd[1])
            elif name == "play_next":
                # El operador pide "siguiente": tambien vale lo planificado
                if not self._current_track and not self._advance(permitir_plan=True):
                    self._emit_error(None, "cola_vacia", "No hay nada en cola para reproducir")
            elif name == "skip":
                if not self._advance(permitir_plan=True):
                    self._do_stop("skip_sin_siguiente")
            elif name == "seek":
                self._do_seek(cmd[1])
            elif name == "pausar":
                self._do_pausar(cmd[1])
            elif name == "cola_insertar":
                self._cola_insertar(cmd[1], cmd[2])
            elif name == "cola_mover":
                self._cola_mover(cmd[1], cmd[2])
            elif name == "cola_quitar":
                self._cola_quitar(cmd[1])
            elif name == "plan_a_cola":
                self._plan_a_cola(cmd[1], cmd[2])
            elif name == "detener_al_terminar":
                self._detener_al_terminar = bool(cmd[1])
                self.bus.emit("detener_al_terminar", {"activo": self._detener_al_terminar,
                                                      "retenido": self._retenido})
            elif name == "plan_insertar":
                self._plan_insertar(cmd[1], cmd[2])
            elif name == "plan_quitar":
                self._plan_quitar(cmd[1])
            elif name == "plan_mover":
                self._plan_mover(cmd[1], cmd[2])
            elif name == "plan_regenerar":
                self._vaciar_programa()
                self._replanificar = True
                self._actualizar_programa()
            elif name == "plan_recalcular":
                self._plan_recalcular_dinamico()

            elif name == "stop":
                self._do_stop("manual")
            elif name == "effect":
                self._do_effect(cmd[1], cmd[2], cmd[3] if len(cmd) > 3 else {})
            elif name == "recover":
                self._do_recover()
            elif name == "restart":
                self._do_restart()
            elif name == "lanzar_tanda":
                self._tanda_liberada = True
                if cmd[1] or not self._current_track:
                    self._advance()
            elif name == "cue":
                self._do_cue(cmd[1], cmd[2])
            elif name == "cue_stop":
                self._do_cue_stop()
            elif name == "tip":
                self._do_tip(cmd[1], cmd[2])
            elif name == "tip_quitar":
                self._tip_pend = None
                self._tip_cancelar("operador")

    # ── Seleccion y arranque de pistas ────────────────────────────────────────

    def _advance(self, permitir_plan: Optional[bool] = None) -> bool:
        """Arranca el siguiente elemento disponible. False si no hay ninguno."""
        for _ in range(5):
            item = self._pick_next(permitir_plan)
            if not item:
                return False
            if self._start(item):
                return True
        return False

    def _pick_next(self, permitir_plan: Optional[bool] = None) -> Optional[dict]:
        """
        Prioridad: pautas liberadas > cola del operador > programa (relojes)
        > lista de respaldo. El programa se consume solo en automatico, o en
        manual cuando el operador pide "siguiente" (permitir_plan=True).
        """
        if permitir_plan is None:
            permitir_plan = self._mode == "automatico"
        now = time.monotonic()
        self._failed_ids = {i: t for i, t in self._failed_ids.items() if now - t < self.FAILED_TTL}

        for _ in range(200):
            item = self._pop_pauta()
            if item:
                return item

            while self._playlist_queue:
                queued = self._playlist_queue.popleft()
                self._emit_queue()
                track = self._fetch_track(queued["id"])
                if track:
                    return {"track": track, "origen": "cola", "nota": queued.get("nota") or ""}

            if not permitir_plan:
                return None
            if not self._programa:
                self._actualizar_programa(forzar=True)
            with self._programa_lock:
                it = self._programa.popleft() if self._programa else None
            if it is None:
                break
            self._emit_programa()
            if it["tipo"] == "tanda":
                # Marcador de tanda: la tanda sale aqui aunque falten unos
                # segundos para su minuto (compensacion del planificador).
                self._check_pautas(forzar=(it["slot"], it["minuto"]))
                continue
            if it.get("tip_modo") in TIP_MODOS:
                # Fila de Tips sobre la música: no suena sola
                if it["tip_modo"] != "puente" and it["audio_id"] not in self._failed_ids:
                    tip = self._fetch_track(it["audio_id"])
                    if tip:
                        self._tip_pend = {"track": tip, "modo": it["tip_modo"], "origen": "auto"}
                        self.bus.emit("tip_programado", self._tip_info(self._tip_pend))
                continue                         # el puente que no llegó a sonar se descarta
            if it.get("evento_key"):
                hoy = self._now().date()
                self._eventos_emitidos = {k for k in self._eventos_emitidos
                                          if k[:10] >= (hoy - timedelta(days=1)).isoformat()}
                self._eventos_emitidos.add(it["evento_key"])
            track = self._fetch_track(it["audio_id"])
            if track and it["audio_id"] not in self._failed_ids:
                return {"track": track, "origen": "auto", "reloj": it.get("reloj"),
                        "reloj_id": it.get("reloj_id"), "fila": it.get("fila"),
                        "plan_uid": it["uid"], "nota": it.get("nota") or ""}

        if self._mode == "automatico":
            return self._pick_respaldo()
        return None

    def _pop_pauta(self) -> Optional[dict]:
        tanda_permitida = self._mode == "automatico" or self._tanda_liberada
        if not self._priority_queue:
            self._tanda_liberada = False
        while tanda_permitida and self._priority_queue:
            pauta = self._priority_queue.popleft()
            if not self._priority_queue:
                self._tanda_liberada = False   # la proxima tanda vuelve a esperar al operador
            track = self._fetch_track(pauta["audio_id"])
            if track:
                return {"track": track, "origen": "pauta", **pauta}
            self._pautas_fallidas.add((pauta["slot"], pauta["pauta_id"]))
        return None

    # ── Programa (relojes) ────────────────────────────────────────────────────

    PLAN_INTERVAL = 5.0

    def _fin_actual(self) -> datetime:
        """Hora estimada en que arranca lo siguiente a lo que suena."""
        now = self._now()
        if self._current_track and self._mix_point != float("inf"):
            pos = self._audio.get_position_sec(self._active_canal)
            return now + timedelta(seconds=max(0.0, self._mix_point - pos))
        return now

    def _vaciar_programa(self):
        with self._programa_lock:
            self._programa.clear()

    def _actualizar_programa(self, forzar: bool = False):
        """Completa el programa hasta `horas_planificadas` desde ahora."""
        if self._mode == "manual":
            return                             # en manual no se planifica: lo arma el operador
        if not forzar and not self._replanificar and \
                time.monotonic() - self._last_plan_check < self.PLAN_INTERVAL:
            return
        self._last_plan_check = time.monotonic()
        self._replanificar = False

        t = self._fin_actual()
        cola = list(self._playlist_queue)
        t += timedelta(seconds=sum(float(q.get("duracion_efectiva") or 180) for q in cola))
        with self._programa_lock:
            actual = list(self._programa)
        for it in actual:
            t += timedelta(seconds=it["duracion"])
        hasta = self._now() + timedelta(hours=self._horas_plan)
        if t >= hasta:
            return
        lanzadas = ({(self._plan_slot, m) for m in self._tandas_lanzadas} if self._plan_slot else set()) | self._cierres_hechos
        try:
            nuevos = self._planner.extender(
                actual, t, hasta, list(self._recent_history), self._tandas_base[0],
                excluir={i for i in self._failed_ids if isinstance(i, int)},
                tandas_emitidas=lanzadas,
                ultimo=self._ultimo_del_reloj(),
                eventos_emitidos=set(self._eventos_emitidos),
            )
        except Exception as exc:
            log.error("[Engine] Planificador: %s", exc, exc_info=True)
            return
        if nuevos:
            with self._programa_lock:
                self._programa.extend(nuevos)
            self._emit_programa()

    def _plan_recalcular_dinamico(self):
        """
        Re-planificación dinámica: vacía el programa actual y lo reconstruye
        respetando estrictamente las horas de las tandas comerciales.
        
        Se llama cuando el operador elimina/mueve una pista desde el Studio,
        cuando el motor detecta aire muerto y salta, o cuando un archivo falla.
        A diferencia de plan_regenerar, calcula el tiempo restante de la pista
        actual para que el recálculo arranque exactamente cuando termina.
        """
        if self._mode == "manual":
            return

        # ── Tiempo restante de la pista actual ──────────────────────────────
        tiempo_restante = 0.0
        if self._current_track and self._mix_point != float("inf"):
            pos = self._audio.get_position_sec(self._active_canal)
            tiempo_restante = max(0.0, self._mix_point - pos)

        # ── Snapshot del plan actual y del historial reciente ────────────────
        with self._programa_lock:
            plan_snapshot = list(self._programa)

        historial = list(self._recent_history)

        lanzadas = (
            {(self._plan_slot, m) for m in self._tandas_lanzadas}
            if self._plan_slot else set()
        ) | self._cierres_hechos

        excluir = {i for i in self._failed_ids if isinstance(i, int)}

        try:
            nuevos = self._planner.recalcular_cola_dinamica(
                plan_actual=plan_snapshot,
                historial=historial,
                tandas_minutos=self._tandas_base[0],
                tiempo_restante_actual=tiempo_restante,
                excluir=excluir,
                tandas_emitidas=lanzadas,
                eventos_emitidos=set(self._eventos_emitidos),
            )
        except Exception as exc:
            log.error("[Engine] recalcular_cola_dinamica: %s", exc, exc_info=True)
            return

        # ── Reemplazar el programa atómicamente ─────────────────────────────
        with self._programa_lock:
            self._programa.clear()
            self._programa.extend(nuevos)

        self._emit_programa()
        log.info(
            "[Engine] Cola recalculada: %d ítems (audios=%d, tandas=%d)",
            len(nuevos),
            sum(1 for i in nuevos if i.get("tipo") == "audio"),
            sum(1 for i in nuevos if i.get("tipo") == "tanda"),
        )

    def plan_recalcular(self):
        """
        API pública: solicita una re-planificación dinámica desde el Studio
        o desde cualquier código externo al hilo del motor.
        Se encola para ejecutarse de forma segura en el hilo del motor.
        """
        self._cmds.put(("plan_recalcular",))



    def _ultimo_del_reloj(self) -> Optional[dict]:
        """Lo que suena ahora, si vino del programa (para seguir el reloj)."""
        it = self._current_item or {}
        if it.get("origen") != "auto" or it.get("fila") is None:
            return None
        return {"inicio": self._now().isoformat(timespec="seconds"),
                "reloj_id": it.get("reloj_id"), "fila": it.get("fila")}

    def _do_pausar(self, activo: bool):
        if activo == self._pausado:
            return
        if activo:
            if not self._current_track:
                return
            self._finish_crossfade(force=True)     # que no quede la anterior sonando
            if not self._audio.pause(self._active_canal):
                self._emit_error(self._current_track.get("id"), "pausa_fallida", "No se pudo pausar el audio")
                return
            if self._cama:
                self._audio.pause(self._cama["canal"])       # la música bajo la voz también
            if self._tip and self._tip["estado"] == "sonando":
                self._audio.pause(CANAL_TIP)
            self._pausado, self._pausado_desde, self._pausa_avisada = True, time.monotonic(), False
            log.info("[Engine] PAUSA en %.1fs de '%s'",
                     self._audio.get_position_sec(self._active_canal), self._current_track.get("titulo"))
        else:
            self._pausado, self._pausado_desde = False, None
            if self._current_track and not self._audio.play(self._active_canal):
                self._emit_error(self._current_track.get("id"), "error_play_backend", "No se pudo reanudar el audio")
            if self._cama:
                self._audio.play(self._cama["canal"])
            if self._tip and self._tip["estado"] == "sonando":
                self._audio.play(CANAL_TIP)
            self._silencio_desde = None
            log.info("[Engine] Se reanuda '%s'", (self._current_track or {}).get("titulo"))
        self.bus.emit("pausa", {"activo": self._pausado})

    def _do_seek(self, segundos: float):
        if not self._current_track or (self._current_item or {}).get("origen") == "pauta":
            log.warning("[Engine] Salto ignorado: nada sonando o es una cuña de pauta")
            return
        self._finish_crossfade(force=True)
        cue_in = self._cue_range(self._current_item)[0]
        fin = self._hard_end if self._hard_end != float("inf") else self._duration
        destino = max(cue_in, min(float(segundos), max(cue_in, fin - 1.0)))
        if self._tip and self._tip["estado"] == "sonando":
            self._tip_cancelar("salto_del_operador")
        if self._audio.set_position_sec(self._active_canal, destino):
            # Si se salta mas alla del punto de mezcla, la siguiente entra enseguida
            self._mix_fired = False
            # Comprobar: hay archivos (MP3 mal formados, VBR sin índice) en los
            # que BASS acepta la orden pero el audio sigue donde estaba.
            real = self._audio.get_position_sec(self._active_canal)
            if abs(real - destino) > 3.0:
                log.error("[Engine] Salto a %.1fs aceptado pero el audio quedó en %.1fs", destino, real)
                self._emit_error(self._current_track.get("id"), "seek_fallido",
                                 f"El archivo no permite adelantar: se pidió {destino:.0f}s y quedó en {real:.0f}s")
                return
            log.info("[Engine] Operador salta a %.1fs", destino)
            self.bus.emit("seek", {"posicion": round(destino, 2)})
        else:
            codigo = getattr(self._audio, "ultimo_error", 0)
            self._emit_error(self._current_track.get("id"), "seek_fallido",
                             f"No se pudo adelantar este audio (error BASS {codigo})")

    def _plan_insertar(self, track: dict, posicion: int):
        from backend.audio.planner import _item
        it = _item("audio", self._now(), duracion_efectiva(track, self._crossfade_secs),
                   audio_id=track["id"], titulo=track["titulo"], artista=track["artista"],
                   tipo_audio=track.get("tipo_audio") or TIPO_MUSICA,
                   genero_vocal=track.get("genero_vocal"), duracion_total=track.get("duracion") or 0,
                   reloj_id=None, reloj=None, color=None, fila=None,
                   compensacion=False, cortada=False, operador=True)
        with self._programa_lock:
            items = list(self._programa)
            items.insert(max(0, min(int(posicion), len(items))), it)
            self._programa = deque(items)
        self._emit_programa()

    def _plan_quitar(self, uid: int):
        item_eliminado = None
        idx_eliminado = -1
        with self._programa_lock:
            for idx, item in enumerate(self._programa):
                if item["uid"] == uid:
                    item_eliminado = item
                    idx_eliminado = idx
                    break
            if item_eliminado is None:
                return
            self._programa = deque(i for i in self._programa if i["uid"] != uid)
        self._replanificar = True
        if self._mode == "automatico" and item_eliminado.get("tipo") == "audio":
            self._ajustar_hueco_tanda(posicion_insertar=idx_eliminado)
        self._emit_programa()

    def _ajustar_hueco_tanda(self, posicion_insertar: int = -1):
        """
        Ajusta la cola del programa cuando se elimina una pista, rellenando el hueco
        temporal para que la próxima tanda comercial no sufra desfase y se emita
        estrictamente a su hora programada.
        """
        if self._mode == "manual":
            return

        with self._programa_lock:
            items = list(self._programa)

        tanda_idx = -1
        tanda = None
        for idx, it in enumerate(items):
            if it.get("tipo") == "tanda":
                tanda_idx = idx
                tanda = it
                break

        if tanda is None or tanda_idx < 0:
            return

        slot = tanda.get("slot")
        minuto = tanda.get("minuto")
        cierre = bool(tanda.get("cierre"))
        dur_tanda = float(tanda.get("duracion") or 0.0)
        if not slot or minuto is None:
            return

        try:
            base = datetime.strptime(slot, "%Y-%m-%d-%H")
            seg = inicio_tanda(minuto, dur_tanda, cierre)
            target_tanda = base + timedelta(seconds=seg)
        except Exception:
            return

        t_cursor = self._fin_actual()
        for q in list(self._playlist_queue):
            t_cursor += timedelta(seconds=float(q.get("duracion_efectiva") or 180.0))

        for k in range(tanda_idx):
            t_cursor += timedelta(seconds=float(items[k].get("duracion") or 0.0))

        brecha = (target_tanda - t_cursor).total_seconds()
        tol = float(getattr(self, "_tolerancia_tanda", None) or (self._planner._icfg("tolerancia_tanda_seg", 90) if hasattr(self, "_planner") else 90.0))

        # Si el adelanto supera los 45 segundos, rellenar con compensación
        if brecha < 45.0:
            return

        log.info(
            "[Engine] Desfase detectado tras quitar pista: brecha de %.1fs antes de la tanda %s (%s). "
            "Ajustando cola con compensación para mantener la hora exacta.",
            brecha, tanda.get("titulo", "TANDA"), target_tanda.strftime("%H:%M:%S")
        )

        en_plan = {x.get("audio_id") for x in items if x.get("audio_id")}
        excluir = {i for i in self._failed_ids if isinstance(i, int)}
        previos = list(self._recent_history) + [
            {"audio_id": x.get("audio_id"), "artista": x.get("artista"),
             "genero_vocal": x.get("genero_vocal"), "tipo_audio": x.get("tipo_audio"),
             "ts": self._now().isoformat()}
            for x in items if x.get("tipo") == "audio"
        ]

        nuevos_items = self._planner.obtener_compensacion_brecha(
            brecha=brecha,
            t_inicio=t_cursor,
            previos=previos,
            en_plan=en_plan,
            excluir=excluir,
            tol=tol,
            crossfade=self._crossfade_secs
        )

        if nuevos_items:
            with self._programa_lock:
                items_act = list(self._programa)
                curr_tanda_idx = next(
                    (i for i, x in enumerate(items_act) if x.get("tipo") == "tanda"),
                    len(items_act)
                )
                pos = curr_tanda_idx if posicion_insertar < 0 else min(posicion_insertar, curr_tanda_idx)
                for nit in reversed(nuevos_items):
                    items_act.insert(pos, nit)
                self._programa = deque(items_act)

            log.info(
                "[Engine] Compensación ajustada: %d pista(s) cubriendo %.1fs. La tanda queda programada para las %s.",
                len(nuevos_items),
                sum(n.get("duracion", 0) for n in nuevos_items),
                target_tanda.strftime("%H:%M:%S")
            )

    def _plan_mover(self, uid: int, posicion: int):
        with self._programa_lock:
            items = list(self._programa)
            it = next((i for i in items if i["uid"] == uid), None)
            if it is None:
                return
            items.remove(it)
            items.insert(max(0, min(int(posicion), len(items))), it)
            self._programa = deque(items)
        self._emit_programa()

    def _emit_programa(self):
        self.bus.emit("programa_actualizado", {"items": len(self._programa)})

    def programa(self) -> dict:
        """
        Lo que viene, con hora estimada de inicio: primero la cola del
        operador y luego el programa de los relojes.
        """
        t = self._fin_actual()
        cola = []
        for q in list(self._playlist_queue):
            cola.append({**q, "hora": t.strftime("%H:%M:%S")})
            t += timedelta(seconds=float(q.get("duracion_efectiva") or 180))
        with self._programa_lock:
            items = [dict(i) for i in self._programa]
        for it in items:
            if it.get("inmediato") and it.get("evento_hora"):
                # Evento inmediato: entra en punto, corte lo que corte
                try:
                    fija = datetime.fromisoformat(it["evento_hora"])
                    if fija > t - timedelta(minutes=10):
                        t = fija
                except ValueError:
                    pass
            if it["tipo"] == "tanda":
                try:
                    base = datetime.strptime(it["slot"], "%Y-%m-%d-%H")
                    cierre = bool(it.get("cierre"))
                    seg = inicio_tanda(it["minuto"], float(it.get("duracion") or 0.0), cierre)
                    fija = base + timedelta(seconds=seg)
                    it["hora_objetivo"] = fija.strftime("%H:%M:%S")
                    if it.get("inmediata") and fija > t - timedelta(minutes=10):
                        t = fija
                    elif cierre and abs((fija - t).total_seconds()) <= 90:
                        # Cuadrar a su minuto fijado de inicio si está dentro de tolerancia
                        t = fija
                except (KeyError, ValueError):
                    pass
            it["hora"] = t.strftime("%H:%M:%S")
            t += timedelta(seconds=it["duracion"])
        items = self._pegar_tips(items, cola)
        return {"modo": self._mode, "horas": self._horas_plan, "cola": cola,
                "items": items, "fin": t.strftime("%H:%M:%S")}

    def _pegar_tips(self, items: list[dict], cola: list[dict]) -> list[dict]:
        """
        Como en SoHo, un Tips de intro o de puente NO es «la canción siguiente»: va encima de la canción a la que
        pisa. En la vista del programa su fila desaparece y la canción lleva `tip` (título y modo):
        de intro → la canción que viene después; de puente → la canción de antes (la que sale).
        """
        def info(t):
            return {"titulo": t.get("titulo", ""), "modo": t["tip_modo"], "audio_id": t.get("audio_id")}
        out: list[dict] = []
        esperando: list[dict] = []          # tips de intro que esperan a la canción que viene
        if self._tip_pend:
            esperando.append({"titulo": self._tip_pend["track"].get("titulo", ""), "tip_modo": self._tip_pend["modo"],
                              "audio_id": self._tip_pend["track"].get("id")})
        for q in cola:
            if esperando and (q.get("tipo_audio") or TIPO_MUSICA) == TIPO_MUSICA:
                q["tip"] = info(esperando.pop(0))
        for it in items:
            if it.get("tip_modo"):
                if it["tip_modo"] == "puente":
                    previo = next((x for x in reversed(out) if x.get("tipo") == "audio"), None)
                    if previo is not None:
                        previo["tip"] = info(it)
                    elif cola:
                        cola[-1]["tip"] = info(it)
                else:
                    esperando.append(it)
                continue
            if esperando and it.get("tipo") == "audio" and it.get("tipo_audio") == TIPO_MUSICA:
                it["tip"] = info(esperando.pop(0))
            out.append(it)
        return out

    def seek(self, segundos: float):
        """
        Adelanta o atrasa lo que suena al aire. No se permite con una cuña
        de pauta: acortarla falsearia lo que se le reporta al cliente.
        """
        item = self._current_item
        if not self._current_track or not item:
            raise ValueError("No hay nada sonando")
        if item.get("origen") == "pauta" or item["track"].get("tipo_audio") == TIPO_CUNA:
            raise ValueError("No se puede adelantar una cuña de pauta")
        self._cmds.put(("seek", float(segundos)))

    def detener_al_terminar(self, activo: bool = True):
        """Stop-next: la pista actual termina y no entra nada mas (ni en automatico)."""
        self._cmds.put(("detener_al_terminar", bool(activo)))

    def pausar(self, activo: bool = True):
        """
        PAUSA (operador): congela lo que suena donde está; activo=False lo
        reanuda. Mientras dura no corre el fin de pista, ni el vigilante, ni
        las tandas o eventos en punto; el aire muerto solo avisa.
        """
        self._cmds.put(("pausar", bool(activo)))

    def plan_insertar(self, audio_id: int, posicion: int = 0) -> bool:
        """Mete un audio en el programa en esa posicion (arrastrar desde la biblioteca)."""
        track = self._fetch_track(audio_id)
        if not track:
            return False
        self._cmds.put(("plan_insertar", track, int(posicion)))
        return True

    def plan_quitar(self, uid: int):
        self._cmds.put(("plan_quitar", int(uid)))

    def plan_mover(self, uid: int, posicion: int):
        self._cmds.put(("plan_mover", int(uid), int(posicion)))

    def plan_regenerar(self):
        """Vuelve a planificar desde ahora (tras cambiar relojes o la grilla)."""
        self._cmds.put(("plan_regenerar",))

    def _pick_respaldo(self) -> Optional[dict]:
        """
        Ultimo recurso antes del silencio: un archivo de media/respaldo/
        (leido del disco, sin BD), evitando repetir los ultimos que sonaron.
        """
        try:
            archivos = sorted(p for p in self._respaldo_dir.rglob("*")
                              if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
        except OSError:
            archivos = []
        archivos = [p for p in archivos if str(p) not in self._failed_ids]
        if not archivos:
            return None
        frescos = [p for p in archivos if str(p) not in self._respaldo_recent] or archivos
        elegido = random.choice(frescos)
        self._respaldo_recent.append(str(elegido))
        log.warning("[Engine] Sin rotacion disponible -> lista de respaldo: %s", elegido.name)
        self.bus.emit("respaldo_activado", {"archivo": elegido.name})
        return {
            "track": {
                "id": None, "titulo": elegido.stem, "artista": "RESPALDO",
                "archivo_path": str(elegido), "tipo_audio": TIPO_MUSICA, "duracion": 0,
            },
            "origen": "respaldo",
        }

    def _start(self, item: dict) -> bool:
        """
        Pone `item` al aire. Si hay una pista sonando hace crossfade hacia la
        nueva en el otro canal; si no, arranca directo en el canal A.
        """
        track = item["track"]
        path  = self._path_of(track)

        if not path or not Path(path).exists():
            log.error("[Engine] Archivo no encontrado: '%s' (id=%s)", path, track.get("id"))
            self._mark_failed(item, path)
            self._emit_error(track.get("id"), "archivo_no_encontrado",
                             f"Archivo no encontrado: {track.get('titulo', '')}", path=str(path))
            return False

        self._finish_crossfade(force=True)
        if self._corte_pend:
            self._inmediatas_hechas.discard(self._corte_pend["clave"])      # si sigue en la ventana, se vuelve a preparar
            self._corte_pend = None
        self._prefade = None

        if self._pausado:
            # Lo que estaba en pausa sale sin mezcla: la nueva entra limpia
            self._audio.stop(self._active_canal)
            self._pausado, self._pausado_desde = False, None
            self.bus.emit("pausa", {"activo": False})

        if self._cama:
            self._soltar_cama()                  # la música de fondo deja libre su canal
        tip_cruza = self._tip_antes_de_arrancar(item)

        outgoing_active = (self._current_track is not None
                           and self._audio.is_active(self._active_canal))
        outgoing = self._active_canal
        # Voice track sobre la canción que suena (no sobre cuñas ni sobre otra voz)
        sobre_musica = (self._is_voz(item) and outgoing_active and self._current_item is not None
                        and not self._is_spot(self._current_item) and not self._is_voz(self._current_item))
        canal    = (CANAL_B if outgoing == CANAL_A else CANAL_A) if outgoing_active else CANAL_A

        if not outgoing_active:
            self._audio.stop(CANAL_A)
            self._audio.stop(CANAL_B)

        if not self._audio.load_stream(path, canal, self._devices["aire"]):
            self._mark_failed(item, path)
            self._emit_error(track.get("id"), "error_carga_backend",
                             f"No se pudo abrir el audio: {track.get('titulo', '')}")
            return False

        cue_in, cue_out = self._cue_range(item)
        if cue_in > 0:
            self._audio.set_position_sec(canal, cue_in)

        dur     = self._audio.get_duration_sec(canal) or float(track.get("duracion") or 0)
        is_spot = self._is_spot(item)
        target  = self._target_volume(track)

        if sobre_musica:
            prev, prev_item = self._current_track, self._current_item
            self._audio.set_volume(canal, target)
            if not self._audio.play(canal):
                self._audio.stop(canal)
                self._emit_error(track.get("id"), "error_play_backend",
                                 f"No se pudo reproducir: {track.get('titulo', '')}")
                return False
            self._audio.slide_volume(outgoing, self._target_volume(prev) * self._voz_duck, self.VOZ_BAJAR_MS)
            self._cama = {"canal": outgoing, "track": prev, "item": prev_item, "saliendo": False}
            self.bus.emit("voz_sobre_musica", {"voz_id": track.get("id"), "musica_id": prev.get("id"),
                                               "titulo": track.get("titulo", "")})
        elif outgoing_active:
            fade_in, fade_out = self._fade_times(self._current_item, item)
            if tip_cruza:
                fade_in, fade_out = min(fade_in, self.TIP_XFADE), min(fade_out, self.TIP_XFADE)
            if self._corte_fade is not None:
                fade_out = max(fade_out, self._corte_fade)   # corte inmediato: la canción sale con calma
            prev = self._current_track
            self._audio.set_volume(canal, 0.0)
            if not self._audio.play(canal):
                self._audio.stop(canal)
                self._emit_error(track.get("id"), "error_play_backend",
                                 f"No se pudo reproducir: {track.get('titulo', '')}")
                return False
            corte = self._corte_fade is not None
            self._audio.slide_volume(canal, target, int(fade_in * 1000))
            if not corte:
                self._audio.slide_volume(outgoing, 0.0, int(fade_out * 1000))
            fade = max(fade_in, fade_out)
            self._xfade = {"canal": outgoing, "until": time.monotonic() + fade, "track": prev}
            if corte:
                # La rampa lineal de BASS casi no se oye al principio y la música "desaparece" de golpe al final:
                # el corte inmediato baja en decibelios (curva de consola), como un fader.
                self._fade_db(outgoing, fade_out, self._target_volume(prev), final_cero=True)
            self.bus.emit("crossfade_start", {
                "from_id": prev.get("id"),
                "to_id":   track.get("id"),
                "duracion_crossfade": fade,
                "duracion_seg":       fade,
            })
        else:
            # Arranque en frio: respeta el fade_in propio de la pista.
            fade_in = 0.0 if (is_spot or item.get("_restart")) else float(track.get("fade_in") or 0)
            self._audio.set_volume(canal, 0.0 if fade_in > 0 else target)
            if not self._audio.play(canal):
                self._audio.stop(canal)
                self._emit_error(track.get("id"), "error_play_backend",
                                 f"No se pudo reproducir: {track.get('titulo', '')}")
                return False
            if fade_in > 0:
                self._audio.slide_volume(canal, target, int(fade_in * 1000))

        # Punto de mezcla (donde arranca la siguiente):
        #   musica: cue_mezcla > outro > duracion - fade_out > duracion - crossfade
        #   cuñas:  suenan completas (o hasta el cue_outro de la pauta) y se
        #           empalman justo al final.
        fin = cue_out if cue_out > 0 else dur
        if dur <= 0:
            mix = float("inf")
        elif is_spot:
            mix = max(fin - self.SPOT_FADE_OUT, cue_in)
        elif self._is_voz(item):
            # La siguiente entra al final de la voz (o en su marca de mezcla, si la tiene)
            mix = float(track.get("cue_mezcla") or 0)
            if not (cue_in < mix < dur):
                mix = max(dur - self.VOZ_FIN, cue_in)
        else:
            mix = float(track.get("cue_mezcla") or track.get("outro") or 0)
            if not (cue_in < mix < dur):
                fade_out_trk = float(track.get("fade_out") or 0)
                lead = fade_out_trk if 0 < fade_out_trk < dur else min(self._crossfade_secs, dur / 4)
                mix = max(dur - lead, 0.0)

        self._active_canal      = canal
        self._current_track     = track
        self._current_item      = item
        self._duration          = dur
        self._mix_point         = mix
        self._hard_end          = cue_out if cue_out > 0 else float("inf")
        self._mix_fired         = False
        self._should_be_playing = True
        # Fin de la voz del cantante: si después viene un voice track, éste entra ahí
        co = float(track.get("cue_outro") or 0)
        self._outro_voz = co if (not is_spot and not self._is_voz(item) and cue_in < co < mix) else None

        if not item.get("_restart"):
            self._on_track_started(track, item, dur)
        self._tip_tras_arrancar(item, tip_cruza)
        return True

    def _finish_crossfade(self, force: bool = False):
        xf = self._xfade
        if not xf or (not force and time.monotonic() < xf["until"]):
            return
        self._xfade = None
        self._audio.stop(xf["canal"])
        self.bus.emit("track_ended", {"audio_id": xf["track"].get("id"), "completada": True})

    # ── Voice track sobre música ─────────────────────────────────────────────

    @classmethod
    def _es_corto(cls, item: Optional[dict]) -> bool:
        """Cuñas y elementos de la emisora (jingle, sweeper, tip, efecto…): todo lo que no es música ni locución."""
        if not item:
            return False
        if cls._is_spot(item):
            return True
        return item["track"].get("tipo_audio") not in (TIPO_MUSICA, TIPO_VOZ, None, "")

    @staticmethod
    def _is_voz(item: Optional[dict]) -> bool:
        return bool(item) and item["track"].get("tipo_audio") == TIPO_VOZ

    def _siguiente_es_voz(self) -> bool:
        """¿Lo próximo que va a sonar es un voice track? (sin sacarlo de su lista)"""
        if self._priority_queue and (self._mode == "automatico" or self._tanda_liberada):
            return False                         # antes sale la tanda
        if self._playlist_queue:
            return self._playlist_queue[0].get("tipo_audio") == TIPO_VOZ
        if self._mode != "automatico":
            return False
        with self._programa_lock:
            it = self._programa[0] if self._programa else None
        return bool(it) and it.get("tipo") == "audio" and it.get("tipo_audio") == TIPO_VOZ

    def _hay_siguiente(self) -> bool:
        """¿Algo va a entrar cuando termine lo que suena? (para despedir o no la música de fondo)"""
        if self._detener_al_terminar:
            return False
        return (self._mode == "automatico" or bool(self._playlist_queue)
                or bool(self._priority_queue and self._tanda_liberada))

    def _vigilar_cama(self):
        """La música bajo la voz: se despide antes de que termine la voz (si algo sigue) o se va sola."""
        c = self._cama
        if not self._audio.is_active(c["canal"]):
            self._audio.stop(c["canal"])
            self._cama = None
            self.bus.emit("track_ended", {"audio_id": c["track"].get("id"), "completada": True})
            return
        if (not c["saliendo"] and self._current_track and self._mix_point != float("inf")
                and self._audio.get_position_sec(self._active_canal) >= self._mix_point - self.VOZ_SALIDA_CAMA
                and self._hay_siguiente()):
            c["saliendo"] = True
            self._audio.slide_volume(c["canal"], 0.0, int(self.VOZ_SALIDA_CAMA * 1000))

    def _soltar_cama(self):
        c, self._cama = self._cama, None
        if c:
            self._audio.stop(c["canal"])
            self.bus.emit("track_ended", {"audio_id": c["track"].get("id"), "completada": True})

    def _recuperar_cama(self) -> bool:
        """Terminó la voz y no viene nada (manual): la canción de fondo vuelve a su volumen y sigue."""
        c, self._cama = self._cama, None
        if not c or not self._audio.is_active(c["canal"]):
            return False
        if self._current_track and self._active_canal != c["canal"] and not self._audio.is_active(self._active_canal):
            self._audio.stop(self._active_canal)
        elif self._current_track and self._active_canal != c["canal"]:
            # la voz termina sola en su canal; queda como crossfade corto
            self._xfade = {"canal": self._active_canal, "until": time.monotonic() + 0.5, "track": self._current_track}
        self._active_canal  = c["canal"]
        self._current_track = c["track"]
        self._current_item  = c["item"]
        self._duration      = self._audio.get_duration_sec(c["canal"]) or float(c["track"].get("duracion") or 0)
        self._mix_point     = float("inf")       # ya pasó su mezcla: termina completa
        self._hard_end      = float("inf")
        self._mix_fired     = True
        self._outro_voz     = None
        self._audio.slide_volume(c["canal"], self._target_volume(c["track"]), self.VOZ_BAJAR_MS)
        self.bus.emit("voz_fin_sin_siguiente", {"musica_id": c["track"].get("id")})
        return True

    # ── Tips sobre el intro / puente (estilo Jazler SoHo) ─────────────────────

    @staticmethod
    def _es_musica(item: Optional[dict]) -> bool:
        return bool(item) and item.get("origen") != "pauta" and \
            (item["track"].get("tipo_audio") or TIPO_MUSICA) == TIPO_MUSICA

    @staticmethod
    def _intro_de(track: dict) -> float:
        return float(track.get("cue_intro") or track.get("intro") or 0)

    def _tip_info(self, t: Optional[dict]) -> Optional[dict]:
        if not t:
            return None
        return {"audio_id": t["track"].get("id"), "titulo": t["track"].get("titulo", ""), "modo": t["modo"],
                "estado": t.get("estado", "pendiente"), "origen": t.get("origen"),
                "cancion_id": (t.get("item") or {}).get("track", {}).get("id"),
                "disparo": round(t["disparo"], 2) if t.get("disparo") is not None else None,
                "limite": round(t["limite"], 2) if t.get("limite") is not None else None,
                "duracion": round(t["dur"], 2) if t.get("dur") is not None else None,
                "recorta": bool(t.get("recorta"))}

    def tip_programar(self, audio_id: int, modo: str) -> bool:
        """Operador (modo manual): monta un Tips sobre el intro de la próxima canción o en el puente de la que suena."""
        if modo not in TIP_MODOS:
            raise ValueError(f"Modo de tip inválido: {modo!r}")
        track = self._fetch_track(audio_id)
        if not track:
            return False
        self._cmds.put(("tip", track, modo))
        return True

    def tip_quitar(self):
        self._cmds.put(("tip_quitar",))

    def _do_tip(self, track: dict, modo: str):
        if modo == "puente":
            if not (self._current_track and self._es_musica(self._current_item)
                    and self._audio.is_active(self._active_canal)):
                self._emit_error(track.get("id"), "tip_sin_cancion", "Para el puente tiene que estar sonando una canción")
                return
            self._tip_cancelar("reemplazado")
            self._tip_montar(track, modo, "manual")
        else:
            self._tip_pend = {"track": track, "modo": modo, "origen": "manual"}
            self.bus.emit("tip_programado", self._tip_info(self._tip_pend))

    def _tip_montar(self, tip_track: dict, modo: str, origen: str, plan_uid: Optional[int] = None) -> bool:
        """Pega el tip a la canción que suena (recién arrancada para los de intro). Lo carga ya para saber su duración real."""
        path = self._path_of(tip_track)
        if not path or not Path(path).exists() or not self._audio.load_stream(path, CANAL_TIP, self._devices["aire"]):
            self._emit_error(tip_track.get("id"), "tip_no_carga", f"No se pudo abrir el tip: {tip_track.get('titulo', '')}")
            return False
        tip_in = float(tip_track.get("cue_inicio") or 0)
        if tip_in > 0:
            self._audio.set_position_sec(CANAL_TIP, tip_in)
        dur = (self._audio.get_duration_sec(CANAL_TIP) or float(tip_track.get("duracion") or 0)) - tip_in
        item = self._current_item
        cue_in = self._cue_range(item)[0]
        mezcla = self._mix_point if self._mix_point != float("inf") else (self._duration or cue_in)
        disparo, limite = calcular_tip(modo, dur, cue_in, self._intro_de(self._current_track), mezcla)
        if limite is None and modo != "puente":
            limite = mezcla if mezcla > cue_in else None    # tip más largo que la canción: se va antes de la siguiente
        self._tip = {"track": tip_track, "modo": modo, "origen": origen, "plan_uid": plan_uid,
                     "item": item, "canal": self._active_canal, "disparo": disparo, "limite": limite,
                     "dur": dur, "estado": "espera", "duck": False, "saliendo": False, "corta_en": None,
                     "recorta": limite is not None and disparo + dur > limite + 0.05}
        self.bus.emit("tip_programado", self._tip_info(self._tip))
        log.info("[Engine] Tip «%s» (%s) programado a %.1fs de «%s»", tip_track.get("titulo"), modo,
                 disparo, self._current_track.get("titulo"))
        return True

    def _proximo_es_musica(self, saltar_uid: Optional[int] = None) -> bool:
        """¿Lo que entra después de la canción es otra canción? (el puente no se monta sobre una tanda ni una voz)"""
        if self._detener_al_terminar:
            return False
        if self._priority_queue and (self._mode == "automatico" or self._tanda_liberada):
            return False
        if self._playlist_queue:
            return (self._playlist_queue[0].get("tipo_audio") or TIPO_MUSICA) == TIPO_MUSICA
        if self._mode != "automatico":
            return False
        with self._programa_lock:
            for it in self._programa:
                if (saltar_uid is not None and it.get("uid") == saltar_uid) or it.get("tip_modo"):
                    continue
                return it.get("tipo") == "audio" and it.get("tipo_audio") == TIPO_MUSICA
        return False

    def _tip_disparar(self):
        t = self._tip
        if t["plan_uid"] is not None and not t.get("cruzo"):
            # Del reloj: sale del programa al sonar (si el operador lo quitó, ya no suena)
            with self._programa_lock:
                antes = len(self._programa)
                self._programa = deque(i for i in self._programa if i.get("uid") != t["plan_uid"])
                quitado = len(self._programa) != antes
            if not quitado:
                self._tip_cancelar("quitado_del_programa")
                return
            self._emit_programa()
        self._audio.set_volume(CANAL_TIP, self._master_volume * self._gain_of(t["track"]))  # a pleno, como un jingle
        if not self._audio.play(CANAL_TIP):
            self._tip_cancelar("error_play")
            return
        t["estado"], t["duck"] = "sonando", True
        if self._current_track:
            self._audio.slide_volume(self._active_canal, self._target_volume(), self.TIP_BAJAR_MS)
        self._log_emision(t["track"], {"origen": t["origen"]}, tipo="tips")
        self.bus.emit("tip_al_aire", self._tip_info(t))

    def _tip_salir(self, segundos: float):
        """Fundido del tip; la música vuelve a pleno mientras tanto."""
        t = self._tip
        t["saliendo"], t["corta_en"] = True, time.monotonic() + segundos
        self._audio.slide_volume(CANAL_TIP, 0.0, int(segundos * 1000))
        if t["duck"]:
            t["duck"] = False
            if self._current_track:
                self._audio.slide_volume(self._active_canal, self._target_volume(), int(segundos * 1000))

    def _tip_fin(self, motivo: str):
        t, self._tip = self._tip, None
        if not t:
            return
        self._audio.stop(CANAL_TIP)
        if t["duck"] and self._current_track and not self._pausado:
            self._audio.slide_volume(self._active_canal, self._target_volume(), self.TIP_SUBIR_MS)
        self.bus.emit("tip_fin", {**self._tip_info(t), "motivo": motivo})

    def _tip_cancelar(self, motivo: str, fundido: bool = True):
        """Quita el tip: si aún no sonaba, se descarta; si suena, sale con un fundido corto (o ya, sin fundido)."""
        t = self._tip
        if not t:
            return
        if t["estado"] == "sonando" and fundido and not t["saliendo"]:
            self._tip_salir(self.TIP_SALIDA)
            t["motivo"] = motivo
        elif t["estado"] != "sonando" or not fundido:
            if t["estado"] != "sonando":
                log.info("[Engine] Tip «%s» no sale: %s", t["track"].get("titulo"), motivo)
            self._tip_fin(motivo)

    def _vigilar_tip(self):
        t = self._tip
        if not t or self._pausado:
            return
        if t["estado"] == "espera" and t.get("disparo_en") is not None:
            # Puente que aún no sonaba cuando entró la siguiente: sale a su hora (la anterior sigue en el fundido)
            if self._current_item is not t["item"]:
                self._tip_cancelar("la_cancion_cambio")
            elif time.monotonic() >= t["disparo_en"]:
                self._tip_disparar()
            return
        if t["estado"] == "espera":
            if self._current_item is not t["item"] or self._active_canal != t["canal"]:
                self._tip_cancelar("la_cancion_cambio")
                return
            pos = self._audio.get_position_sec(t["canal"])
            if t["limite"] is not None and pos >= t["limite"] - self.TIP_CORTE:
                self._tip_cancelar("ya_paso_el_intro")         # p. ej. el operador adelantó la canción
                self._emit_error(t["track"].get("id"), "tip_no_sale", f"El tip «{t['track'].get('titulo', '')}» no salió: ya pasó el intro")
            elif pos >= t["disparo"]:
                if t["modo"] == "puente" and not self._proximo_es_musica(t["plan_uid"]):
                    self._tip_cancelar("no_sigue_una_cancion")
                    self._emit_error(t["track"].get("id"), "tip_no_sale",
                                     f"El puente «{t['track'].get('titulo', '')}» no salió: después no viene una canción (agrégala a la cola)")
                else:
                    self._tip_disparar()
            return
        # sonando
        if not self._audio.is_active(CANAL_TIP):
            self._tip_fin(t.get("motivo") or "fin")
            return
        if t["saliendo"]:
            pos = self._audio.get_position_sec(t["canal"])
            if time.monotonic() >= t["corta_en"] or (t["limite"] is not None and pos >= t["limite"]):
                self._tip_fin(t.get("motivo") or "recortado")
            return
        if t["limite"] is not None and self._audio.get_position_sec(t["canal"]) >= t["limite"] - self.TIP_CORTE:
            self._tip_salir(self.TIP_CORTE)                     # nunca tapa la entrada del cantante
            return
        resta = self._audio.get_duration_sec(CANAL_TIP) - self._audio.get_position_sec(CANAL_TIP)
        if t["duck"] and resta <= self.TIP_SUBE_ANTES:
            # La música sube mientras el tip termina (como suelta un operador el fader): a pleno cuando calla
            t["duck"] = False
            if self._current_track:
                self._audio.slide_volume(self._active_canal, self._target_volume(), int(max(resta, 0.3) * 1000))

    def _tip_antes_de_arrancar(self, item: dict) -> bool:
        """
        Algo nuevo va al aire. El puente que suena sigue sobre la canción siguiente; cualquier otro tip
        se va (una cuña, una voz o un salto del operador no se tapan). True = el tip cruza a esta canción.
        """
        t = self._tip
        if not t:
            return False
        if (t["modo"] == "puente" and not t["saliendo"] and not t.get("cruzo") and self._es_musica(item)
                and (t["estado"] == "sonando" or (self._current_item is t["item"] and self._mix_fired))):
            if t["estado"] == "espera":
                # La siguiente entra (en la mezcla) antes de que la anterior llegue a su final: el tip
                # igual sale cuando a la anterior le quede la mitad del tip
                pos = self._audio.get_position_sec(t["canal"])
                t["disparo_en"] = time.monotonic() + max(0.0, t["disparo"] - pos)
            t["cruzo"] = True
            return True
        self._tip_cancelar("entra_otro_audio")
        if self._tip and self._tip["estado"] == "sonando":
            self._tip["duck"] = False                           # lo que entra ya no queda debajo del tip
        return False

    def _tip_tras_arrancar(self, item: dict, cruza: bool):
        if cruza and self._tip:
            # Mitad del puente sobre la canción nueva: tampoco pisa su voz
            t = self._tip
            t["item"], t["canal"] = item, self._active_canal
            cue_in, intro = self._cue_range(item)[0], self._intro_de(item["track"])
            t["limite"] = intro if intro > cue_in else None
            return
        if not self._es_musica(item) or self._tip:
            return
        if self._tip_pend:
            p, self._tip_pend = self._tip_pend, None
            self._tip_montar(p["track"], p["modo"], p["origen"], p.get("plan_uid"))
            self._vigilar_tip()                                 # «inicio del intro» sale ya
            return
        if self._mode == "automatico" and item.get("origen") == "auto":
            # Puente del reloj: la fila de Tips que viene justo después de esta canción
            with self._programa_lock:
                sig = self._programa[0] if self._programa else None
            if sig and sig.get("tip_modo") == "puente":
                tip = self._fetch_track(sig["audio_id"])
                if tip:
                    self._tip_montar(tip, "puente", "auto", sig["uid"])

    def _do_stop(self, reason: str):
        self._finish_crossfade(force=True)
        self._tip_pend = None
        self._tip_cancelar("stop", fundido=False)
        self._cama = None                        # A y B se detienen abajo
        if self._pausado:
            self._pausado, self._pausado_desde = False, None
            self.bus.emit("pausa", {"activo": False})
        track = self._current_track
        self._audio.stop(CANAL_A)
        self._audio.stop(CANAL_B)
        self._current_track     = None
        self._current_item      = None
        self._should_be_playing = False
        self._idle_retry_at     = time.monotonic() + self.IDLE_RETRY
        if track:
            self.bus.emit("track_ended", {"audio_id": track.get("id"), "completada": False})
        self.bus.emit("playback_stopped", {"reason": reason})

    @staticmethod
    def _path_of(track: dict) -> str:
        """Ruta real en disco del audio (archivo_path se guarda relativo a MEDIA_ROOT)."""
        stored = track.get("archivo_path") or ""
        return str(resolve_media(stored)) if stored else ""

    @staticmethod
    def _is_spot(item: dict) -> bool:
        """Las cuñas suenan completas: no se les aplica el crossfade musical."""
        return item.get("origen") == "pauta" or item["track"].get("tipo_audio") == TIPO_CUNA

    @staticmethod
    def _cue_range(item: dict) -> tuple[float, float]:
        """
        (inicio, fin) de reproduccion. fin = 0 significa "hasta el final".
        Una pauta puede recortar su cuña con sus propios cue_intro/cue_outro
        (marcados en el editor de pautas); si no, vale el cue_inicio del audio.
        """
        cue_in, cue_out = float(item["track"].get("cue_inicio") or 0), 0.0
        if item.get("origen") == "pauta":
            p_in  = float(item.get("cue_intro") or 0)
            p_out = float(item.get("cue_outro") or 0)
            if p_in > 0:
                cue_in = p_in
            if p_out > cue_in:
                cue_out = p_out
        return cue_in, cue_out

    def _fade_db(self, canal: int, segundos: float, vol_inicial: float, paso: float = 0.1,
                 mientras: Optional[Callable] = None, final_cero: bool = False) -> None:
        """
        Baja el canal `canal` desde `vol_inicial` siguiendo la curva del corte (config fade_corte_curva, % repartidos
        en partes iguales del fade: por defecto 100 → 70 → 40 → 30 → 16). El último punto NO es silencio: la cuña entra con
        la canción todavía audible y esa cola se va con el empalme de la cuña. `final_cero` lo lleva a 0 (empalme tardío).
        Corre en su propio hilo; se corta sola si el canal deja de ser el que sale.
        """
        curva = self._fade_curva or list(CURVA_FADE_DEFECTO)
        n = max(1, int(round(segundos / paso)))

        def nivel(u: float) -> float:
            pos = max(0.0, min(1.0, u)) * (len(curva) - 1)
            i = int(pos)
            if i >= len(curva) - 1:
                return curva[-1]
            return curva[i] + (curva[i + 1] - curva[i]) * (pos - i)

        def correr():
            for i in range(1, n + 1):
                if mientras is not None:
                    if not mientras():
                        return
                else:
                    xf = self._xfade
                    if not xf or xf.get("canal") != canal:
                        return
                v = 0.0 if (final_cero and i == n) else vol_inicial * nivel(i / n)
                self._audio.slide_volume(canal, v, int(paso * 1000))
                time.sleep(paso)

        threading.Thread(target=correr, name="onix-fade-corte", daemon=True).start()

    def _prefade_iniciar(self, segundos: float) -> bool:
        """
        Empieza a bajar la canción que suena (curva en dB) para que llegue a silencio justo cuando entre
        la cuña. La cuña NO arranca hasta que el fade termina: así no lo tapa. False si no suena música.
        """
        canal, prev, item = self._active_canal, self._current_track, self._current_item
        if prev is None or not item or item.get("origen") == "pauta" or self._is_spot(item) \
                or not self._audio.is_active(canal):
            return False
        vol = self._target_volume(prev)
        self._prefade = {"canal": canal, "vol": vol}
        self._fade_db(canal, max(0.3, segundos), vol,
                      mientras=lambda: bool(self._prefade) and self._prefade["canal"] == canal and self._active_canal == canal)
        return True

    def _fade_times(self, prev_item: Optional[dict], item: dict) -> tuple[float, float]:
        """
        Duracion (s) de la entrada de `item` y de la salida de `prev_item`.
        Entre canciones vale el fade_in / fade_out de cada pista y, si no
        tienen, el crossfade general. Con cuñas de por medio se usan los
        empalmes cortos fijos (la musica no debe quedar debajo de la cuña).
        """
        if self._es_corto(item):
            # La cuña / jingle / sweeper / tip entra YA a su volumen (ataque de 50 ms solo para evitar el clic):
            # una rampa lenta la dejaba sonando bajo al principio. Lo que sale es lo que se desvanece.
            return self.ENTRADA_CORTO, (self.SPOT_FADE_OUT if (prev_item and self._es_corto(prev_item)) else self.SPOT_FADE_IN)
        if prev_item and self._es_corto(prev_item):
            return self.SPOT_FADE_OUT, self.SPOT_FADE_OUT
        if self._is_voz(item):
            return 0.1, 0.5                      # la voz entra limpia
        if prev_item and self._is_voz(prev_item):
            # tras la voz, la canción entra casi a pleno (como el locutor que "pisa" la intro)
            return (float(item["track"].get("fade_in") or 0) or 0.3), 0.3
        fade_in  = float(item["track"].get("fade_in") or 0) or min(self.ENTRADA_CANCION, self._crossfade_secs)
        fade_out = (float(prev_item["track"].get("fade_out") or 0) if prev_item else 0) \
            or self._crossfade_secs
        return fade_in, fade_out

    def _mark_failed(self, item: dict, path) -> None:
        """Evita reintentar enseguida un audio (o pauta) cuyo archivo fallo."""
        track = item["track"]
        self._failed_ids[track.get("id") if track.get("id") is not None else str(path)] = time.monotonic()
        if item.get("origen") == "pauta":
            self._pautas_fallidas.add((item.get("slot"), item.get("pauta_id")))

    def _gain_of(self, track: Optional[dict]) -> float:
        """
        Factor lineal de volumen del audio: normalizacion al objetivo LUFS
        (si esta activa y el audio ya fue medido) mas el ajuste manual
        ganancia_db, sin pasar el pico de -1 dBTP ni +6 dB.
        """
        track = track or {}
        try:
            manual = float(track.get("ganancia_db") or 0)
            lufs   = track.get("loudness_lufs")
            pico   = track.get("pico_db")
            if self._norm_activa and lufs is not None:
                db = ganancia_db(float(lufs), None if pico is None else float(pico),
                                 self._norm_lufs + manual, max_boost=MAX_BOOST_DB)
            else:
                db = max(-30.0, min(MAX_BOOST_DB, manual))
        except (TypeError, ValueError):
            return 1.0
        return 10 ** (db / 20)

    def _target_volume(self, track: Optional[dict] = None) -> float:
        track = track if track is not None else self._current_track
        duck  = self._ducking_vol if self._effect_ducking else 1.0
        if self._tip and self._tip.get("duck"):
            duck *= self._tip_duck               # bajo un tip la música baja menos que bajo una voz
        return self._master_volume * duck * self._gain_of(track)

    # ── Cartuchera ────────────────────────────────────────────────────────────

    def _do_effect(self, track: dict, canal_offset: int, op: Optional[dict] = None):
        op    = op or {}
        path  = self._path_of(track)
        canal = CANAL_EFX_BASE + (int(canal_offset) % 6)
        boton = op.get("boton") or None

        # Mismo botón y todavía suena: se detiene (pulsar = sonar, pulsar otra vez = parar)
        actual = self._efx.get(canal)
        if boton and actual and actual.get("boton") == boton and self._audio.is_active(canal):
            self._stop_effect(canal, "operador")
            return
        if actual:
            self._stop_effect(canal, "reemplazado")   # otro botón comparte el canal: el anterior se corta

        if not path or not Path(path).exists():
            self._emit_error(track.get("id"), "archivo_no_encontrado",
                             f"Efecto no encontrado: {track.get('titulo', '')}", path=str(path))
            return
        if not self._audio.load_stream(path, canal, self._device_de("cartuchera")):
            self._emit_error(track.get("id"), "error_carga_backend",
                             f"No se pudo abrir el efecto: {track.get('titulo', '')}")
            return

        dur = self._audio.get_duration_sec(canal) or float(track.get("duracion") or 5)
        # Marcas del botón: fuera de rango o cruzadas se ignoran (suena el archivo completo)
        try:
            inicio = float(op.get("mark_in") or 0)
            fin    = float(op.get("mark_out") or 0)
            vol    = max(0.0, min(float(op["volumen"] if op.get("volumen") is not None else 100), 150.0)) / 100
        except (TypeError, ValueError):
            inicio, fin, vol = 0.0, 0.0, 1.0
        if not (0 < inicio < dur):
            inicio = 0.0
        if not (inicio < fin < dur):
            fin = 0.0                                  # 0 = hasta el final del archivo
        if inicio > 0 and not self._audio.set_position_sec(canal, inicio):
            inicio = 0.0

        self._audio.set_volume(canal, self._master_volume * self._gain_of(track) * vol)
        self._audio.play(canal)

        largo = (fin or dur) - inicio
        self._efx[canal] = {"boton": boton, "audio_id": track["id"], "titulo": track.get("titulo", ""),
                            "fin": fin, "hasta": time.monotonic() + largo}
        self._duck_until = max(self._duck_until, time.monotonic() + largo + 0.3)
        if not self._effect_ducking:
            self._effect_ducking = True
            if self._current_track:
                self._audio.slide_volume(self._active_canal, self._target_volume(), 200)

        self._log_emision(track, {"origen": "manual"}, tipo="efecto")
        self.bus.emit("effect_fired", {
            "audio_id": track["id"], "titulo": track.get("titulo", ""), "canal": canal,
            "duracion": round(largo, 2), "boton": boton,
        })
        log.info("[Engine] Efecto: %s (canal %d, %.1fs)", track.get("titulo"), canal, largo)

    def _stop_effect(self, canal: int, motivo: str):
        """Corta el efecto de ese canal y devuelve la música a su volumen si ya no suena ninguno."""
        e = self._efx.pop(canal, None)
        self._audio.stop(canal)
        if e is None:
            return
        # el ducking dura lo que el efecto más largo que siga sonando; sin ninguno, se suelta ya
        self._duck_until = max((x["hasta"] + 0.3 for x in self._efx.values()), default=time.monotonic())
        self.bus.emit("effect_stopped", {"audio_id": e["audio_id"], "titulo": e["titulo"], "canal": canal,
                                         "boton": e["boton"], "motivo": motivo})
        if motivo != "fin":
            log.info("[Engine] Efecto detenido (%s): %s", motivo, e["titulo"])

    def _vigilar_efectos(self):
        """Corta cada efecto en su marca out y avisa cuando termina (para apagar el botón en el Studio)."""
        for canal, e in list(self._efx.items()):
            if not self._audio.is_active(canal):
                self._stop_effect(canal, "fin")
            elif e["fin"] and self._audio.get_position_sec(canal) >= e["fin"]:
                self._stop_effect(canal, "fin")

    # ── Preescucha (salida interna, nunca al aire) ───────────────────────────

    def _do_cue(self, track: dict, desde: float):
        path = self._path_of(track)
        if not path or not Path(path).exists():
            self._emit_error(track.get("id"), "archivo_no_encontrado",
                             f"Preescucha: archivo no encontrado: {track.get('titulo', '')}",
                             path=str(path))
            return
        if not self._audio.load_stream(path, CANAL_CUE, self._devices["preescucha"]):
            self._cue_track = None
            self._emit_error(track.get("id"), "error_preescucha",
                             "No se pudo abrir la preescucha (revisa la tarjeta de preescucha)")
            return
        if desde > 0:
            self._audio.set_position_sec(CANAL_CUE, desde)
        self._audio.set_volume(CANAL_CUE, self._gain_of(track))
        self._audio.play(CANAL_CUE)
        self._cue_track = track
        self.bus.emit("cue_started", {
            "audio_id": track.get("id"), "titulo": track.get("titulo", ""),
            "artista": track.get("artista", ""),
            "duracion": self._audio.get_duration_sec(CANAL_CUE), "desde": desde,
        })

    def _do_cue_stop(self):
        track, self._cue_track = self._cue_track, None
        self._audio.stop(CANAL_CUE)
        if track:
            self.bus.emit("cue_stopped", {"audio_id": track.get("id")})

    def _restore_ducking(self):
        if self._effect_ducking and time.monotonic() >= self._duck_until:
            self._effect_ducking = False
            if self._current_track and not self._xfade:
                self._audio.slide_volume(self._active_canal, self._target_volume(), 400)

    # ── Programacion de pautas ────────────────────────────────────────────────

    def _check_pautas(self, forzar: Optional[tuple] = None):
        """
        Programa las tandas de la hora en curso. Clave de matriz: "dia-hora",
        dia 0=Dom…6=Sab, hora 0–23.

        1. Al empezar cada hora se reparten sus pautas entre las tandas
           (planificar_tandas). Las que se crean a mitad de hora van a la
           proxima tanda que quede (o salen ya si no queda ninguna).
        2. Al llegar el minuto de una tanda, sus cuñas pendientes entran
           juntas a la cola prioritaria y salen en el proximo cambio de pista.
           Si el motor arranca tarde, las tandas ya vencidas salen enseguida.
        3. Lo que no alcanzo a salir en su hora se descarta (no se acumula).
        4. SECCIONES (tanda_secciones, como las prioridades de Jazler): la tanda
           sale ordenada por seccion (ID, cortina de entrada, comerciales,
           cortina de salida…). Solo las secciones comerciales se reparten
           entre las tandas; las demas salen en CADA tanda de sus horas, y las
           opcionales solo si la tanda lleva al menos una cuña comercial.
        """
        if forzar is None and time.monotonic() - self._last_pautas_check < self.PAUTAS_INTERVAL:
            return
        self._last_pautas_check = time.monotonic()
        self._tandas_de_ahora()

        now  = self._now()
        slot = now.strftime("%Y-%m-%d-%H")
        key  = f"{(now.weekday() + 1) % 7}-{now.hour}"
        hoy  = now.date().isoformat()

        # Lo que no salió en su hora pasa a la cola de "Pautas en Deuda" (Cumplimiento Comercial)
        # para recuperarse en las siguientes tandas del día sin perderse.
        vigente = lambda p: p["slot"] == slot or bool(p.get("cierre_hasta") and now < p["cierre_hasta"])
        perdidas = [p for p in self._priority_queue if not vigente(p)]
        if perdidas:
            self._priority_queue = deque(p for p in self._priority_queue if vigente(p))
            for p in perdidas:
                log.warning("[Pautas] No salio en su hora (pasa a deuda): %s (%s)", p.get("cliente"), p.get("slot"))
                self.bus.emit("pauta_missed", p)
                pid = p.get("pauta_id")
                if pid:
                    try:
                        conn_d = _db_conn(self._db)
                        try:
                            conn_d.execute(
                                "INSERT INTO pautas_deuda (pauta_id, slot_original, fecha, motivo) VALUES (?, ?, ?, 'hora_expirada')",
                                (pid, p.get("slot", ""), hoy),
                            )
                            conn_d.commit()
                        finally:
                            conn_d.close()
                    except Exception as e_d:
                        log.error("[Pautas] Error registrando deuda de pauta %s: %s", pid, e_d)


        try:
            conn = _db_conn(self._db)
            try:
                rows = conn.execute(
                    """
                    SELECT p.id, p.cliente, p.audio_id, p.matriz, p.rubro,
                           p.menciones_diarias, p.cue_intro, p.cue_outro,
                           COALESCE(p.seccion, 3) AS seccion, a.duracion,
                           COALESCE(p.posicion, 0) AS posicion, p.orden, a.genero_vocal AS voz
                    FROM pautas p
                    JOIN audios a ON a.id = p.audio_id AND a.activo = 1
                    WHERE p.activo = 1
                      AND (COALESCE(p.fecha_inicio,'') = '' OR p.fecha_inicio <= ?)
                      AND (COALESCE(p.fecha_fin,'')    = '' OR p.fecha_fin    >= ?)
                    ORDER BY COALESCE(p.prioridad, 0) DESC, p.id ASC
                    """,
                    (hoy, hoy),
                ).fetchall()
                emitidas = {
                    r[0] for r in conn.execute(
                        "SELECT pauta_id FROM historial_emision"
                        " WHERE slot = ? AND pauta_id IS NOT NULL", (slot,)
                    )
                }
                por_tanda = {
                    (r[0], r[1]) for r in conn.execute(
                        "SELECT pauta_id, tanda FROM historial_emision"
                        " WHERE slot = ? AND pauta_id IS NOT NULL", (slot,)
                    )
                }
                secciones = {
                    r[0]: {"opcional": bool(r[1]), "comercial": bool(r[2])}
                    for r in conn.execute("SELECT numero, opcional, comercial FROM tanda_secciones")
                }
                versiones: dict[int, list[int]] = {}
                for pa in conn.execute(
                        "SELECT pa.pauta_id, pa.audio_id FROM pauta_audios pa"
                        " JOIN audios a ON a.id = pa.audio_id AND a.activo = 1"
                        " ORDER BY pa.pauta_id, pa.orden"):
                    versiones.setdefault(pa[0], []).append(pa[1])
                total_emitidas = dict(conn.execute(
                    "SELECT pauta_id, COUNT(*) FROM historial_emision"
                    " WHERE pauta_id IS NOT NULL GROUP BY pauta_id").fetchall())
                durs = {r[0]: r[1] or 0 for r in conn.execute(
                    "SELECT id, duracion FROM audios WHERE id IN"
                    " (SELECT audio_id FROM pauta_audios)")}
                voces_v = {r[0]: r[1] for r in conn.execute(
                    "SELECT id, genero_vocal FROM audios WHERE id IN (SELECT audio_id FROM pauta_audios)")}
                del_dia = dict(conn.execute(
                    "SELECT pauta_id, COUNT(*) FROM historial_emision"
                    " WHERE slot LIKE ? AND pauta_id IS NOT NULL GROUP BY pauta_id",
                    (hoy + "-%",),
                ).fetchall())
                ajustes, quitadas = leer_ajustes(conn, hoy, now.hour)
            finally:
                conn.close()
        except Exception as exc:
            log.error("[Pautas] Error consultando pautas: %s", exc)
            return

        de_la_hora = {}
        for row in rows:
            try:
                matriz = json.loads(row["matriz"] or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(matriz, dict) and matriz.get(key):
                # Varias versiones: rotan en cada salida (A, B, C, A…)
                audio_id = version_rotada(versiones.get(row["id"], []),
                                          total_emitidas.get(row["id"], 0)) or row["audio_id"]
                de_la_hora[row["id"]] = {
                    "pauta_id": row["id"], "cliente": row["cliente"],
                    "audio_id": audio_id, "rubro": row["rubro"] or "", "orden": row["orden"],
                    "menciones_diarias": row["menciones_diarias"],
                    "cue_intro": row["cue_intro"], "cue_outro": row["cue_outro"],
                    "seccion": row["seccion"],
                    "duracion": durs.get(audio_id, row["duracion"]) if audio_id != row["audio_id"]
                                else (row["duracion"] or 0),
                    "posicion": row["posicion"], "slot": slot,
                    "voz": voces_v.get(audio_id, row["voz"]) if audio_id != row["audio_id"] else row["voz"],
                }

        # Seccion desconocida = comercial (asi nunca se pierde una cuña de cliente)
        es_comercial = lambda p: secciones.get(p["seccion"], {"comercial": True})["comercial"]
        # Las quitadas a mano para hoy no entran (ni como «pauta nueva»)
        comerciales = {pid: p for pid, p in de_la_hora.items() if es_comercial(p) and pid not in quitadas}
        fijas = [p for p in de_la_hora.values() if not es_comercial(p)]
        self._plan_hour(slot, now.minute, list(comerciales.values()))
        # El orden del bloque (posición fija u orden manual) se aplica en vivo,
        # sin mover cuñas de una tanda a otra
        for m, ids in self._plan.items():
            conocidas = [comerciales[i] for i in ids if i in comerciales]
            self._plan[m] = ([p["pauta_id"] for p in ordenar_tanda(conocidas)]
                             + [i for i in ids if i not in comerciales])
        # Orden a mano del operador (Tandas del día): manda sobre el automático
        if ajustes:
            self._plan = aplicar_ajustes(self._plan, ajustes)

        # forzar=(slot, minuto): el programa llego al marcador de esa tanda
        # unos segundos antes de su minuto; sale ya.
        limite = now.minute
        if forzar and forzar[0] == slot:
            limite = max(limite, forzar[1])
        en_cola = {p["pauta_id"] for p in self._priority_queue}
        en_cola_tanda = {(p["pauta_id"], p.get("tanda")) for p in self._priority_queue}
        seg_hora = now.minute * 60 + now.second
        for minuto in sorted(self._plan):
            extra_cierre = {}
            if minuto in self._tandas_cierre:
                fin_bloque = now.replace(minute=0, second=0, microsecond=0) + (
                    timedelta(hours=1) if minuto == 0 else timedelta(minutes=minuto))
                extra_cierre = {"cierre_hasta": fin_bloque + timedelta(minutes=5)}
            if minuto in self._tandas_cierre:
                # Bloque al cierre: arranca cuando falta lo que dura
                dur = sum(float(comerciales[pid]["duracion"] or 0)
                          for pid in self._plan[minuto] if pid in comerciales)
                dur += sum(float(f["duracion"] or 0) for f in fijas)
                self._dur_cierre[minuto] = dur
                forzada = bool(forzar and forzar == (slot, minuto))
                if not forzada and inicio_tanda(minuto, dur, True) > seg_hora:
                    continue
            elif minuto > limite:
                continue
            lanzadas = []
            for pid in self._plan[minuto]:
                pauta = comerciales.get(pid)
                if (pauta is None or pid in emitidas or pid in en_cola
                        or (slot, pid) in self._pautas_fallidas):
                    continue
                tope = pauta["menciones_diarias"]
                if tope and del_dia.get(pid, 0) >= tope:
                    continue
                pauta = {**pauta, "tanda": minuto, **extra_cierre}
                self._priority_queue.append(pauta)
                en_cola.add(pid)
                lanzadas.append(pauta)
                log.info("[Pautas] Tanda :%02d → %s (pauta %d)", minuto, pauta["cliente"], pid)
                self.bus.emit("pauta_scheduled", pauta)

            # Secciones fijas (ID, cortinas): una vez por tanda
            hay_comercial = any(
                p.get("tanda") == minuto and es_comercial(p) for p in self._priority_queue
            ) or any((pid, minuto) in por_tanda for pid in comerciales)
            for pauta in fijas:
                pid = pauta["pauta_id"]
                if ((pid, minuto) in por_tanda or (pid, minuto) in en_cola_tanda
                        or (slot, pid) in self._pautas_fallidas):
                    continue
                if secciones.get(pauta["seccion"], {}).get("opcional") and not hay_comercial:
                    continue
                tope = pauta["menciones_diarias"]
                if tope and del_dia.get(pid, 0) >= tope:
                    continue
                pauta = {**pauta, "tanda": minuto, **extra_cierre}
                self._priority_queue.append(pauta)
                en_cola_tanda.add((pid, minuto))
                lanzadas.append(pauta)
                self.bus.emit("pauta_scheduled", pauta)

            if lanzadas:
                if minuto in self._tandas_cierre:
                    self._cierres_hechos.add((slot, minuto))
                    self._cierres_hechos = {c for c in self._cierres_hechos
                                            if c[0] >= (now - timedelta(hours=3)).strftime("%Y-%m-%d-%H")}
                # Orden dentro de la tanda: por seccion (estable: respeta el
                # reparto por rubro y prioridad de las comerciales)
                self._priority_queue = deque(sorted(
                    self._priority_queue, key=lambda p: (p.get("tanda") or 0, p.get("seccion") or 0)))
                self._idle_retry_at = 0.0
                if minuto not in self._tandas_lanzadas:
                    self._tandas_lanzadas.add(minuto)
                    duracion = sum(float(p.get("duracion") or 0) for p in self._priority_queue
                                   if p.get("tanda") == minuto)
                    if self._tanda_max_seg and duracion > self._tanda_max_seg:
                        log.warning("[Pautas] Tanda :%02d dura %.0fs (maximo %ds)",
                                    minuto, duracion, self._tanda_max_seg)
                        self.bus.emit("tanda_excedida", {"minuto": minuto, "duracion": round(duracion),
                                                         "maximo": self._tanda_max_seg})
                    self.bus.emit("tanda_scheduled", {
                        "duracion": round(duracion),
                        "minuto": minuto, "slot": slot,
                        "requiere_operador": self._mode == "manual",
                        "pautas": [{"pauta_id": p["pauta_id"], "cliente": p["cliente"]}
                                   for p in lanzadas],
                    })

    def _tandas_de_ahora(self):
        """Con grilla por día y hora, la hora en curso usa sus propias tandas."""
        if not self._tandas_grilla:
            return
        now = self._now()
        m, i, c = tandas_de_hora(self._tandas_grilla, (now.weekday() + 1) % 7, now.hour, *self._tandas_base)
        self._tandas_inmediatas, self._tandas_cierre = i, c
        if m != self._tandas_minutos:
            self._tandas_minutos = m
            self._plan_slot = None

    def _check_tanda_inmediata(self):
        """
        Inicio inmediato (Jazler "Break Starts Immediately"): en automatico, la tanda empieza a su hora exacta.
        La cancion que suena empieza a bajar `fade_corte_seg` segundos ANTES (curva en dB) y la tanda entra cuando
        el fade termina, justo a la hora: la cuña no tapa el fade. En manual decide el operador.
        """
        self._tandas_de_ahora()
        if not self._tandas_inmediatas or self._mode != "automatico" or self._pausado:
            return
        now = self._now()
        self._ejecutar_corte(now)
        if self._corte_pend:
            return
        seg = now.minute * 60 + now.second + now.microsecond / 1e6
        adelanto = max(0.5, self._fade_corte)
        for m in self._tandas_inmediatas:
            if m in self._tandas_cierre:
                # Al cierre + inmediata: entra cuando falta justo lo que dura el bloque, para que termine
                # en su minuto (0 = en punto) y la hora siguiente empiece con música
                dur = self._dur_cierre.get(m)
                fin = (60 if m == 0 else m) * 60
                if dur is None or seg >= fin:
                    continue
                falta = inicio_tanda(m, dur, True) - seg
                if falta > adelanto:
                    continue
            else:
                falta = (m * 60 - seg) % 3600
                if falta >= 3598:
                    falta = 0.0                    # el minuto acaba de pasar
                if falta > adelanto:
                    continue
            falta = max(0.0, falta)
            target = now + timedelta(seconds=falta)
            clave = (target.strftime("%Y-%m-%d-%H"), m)
            if clave in self._inmediatas_hechas:
                continue
            self._inmediatas_hechas = {c for c in self._inmediatas_hechas if c[0] >= clave[0]} | {clave}
            canal = self._active_canal
            if falta > 0.05 and self._prefade_iniciar(min(falta, adelanto)):
                log.info("[Pautas] Tanda inmediata :%02d — la cancion baja %.1f s y la tanda entra a su hora", m, falta)
            self._corte_pend = {"clave": clave, "minuto": m, "target": target, "canal": canal,
                                "vol": (self._prefade or {}).get("vol")}
            self._ejecutar_corte(now)              # si ya es la hora, entra ahora
            return

    def _ejecutar_corte(self, now) -> None:
        """Si ya llegó la hora del corte pendiente: lanza la tanda (la cancion ya bajó; la cuña entra limpia)."""
        p = self._corte_pend
        if not p or now < p["target"]:
            return
        self._corte_pend = None
        canal, vol, minuto = p["canal"], p["vol"], p["minuto"]
        self._prefade = None
        self._check_pautas(forzar=(now.strftime("%Y-%m-%d-%H"), minuto))
        sonando_musica = (self._current_track is not None and self._current_item
                          and self._current_item.get("origen") != "pauta")
        if sonando_musica and any(x.get("tanda") == minuto for x in self._priority_queue):
            log.info("[Pautas] Tanda inmediata :%02d — entra a su hora", minuto)
            self.bus.emit("tanda_inmediata", {"minuto": minuto, "cierre": minuto in self._tandas_cierre})
            self._advance()                        # sin _corte_fade: la cancion ya está en silencio
        elif vol is not None and canal == self._active_canal and self._audio.is_active(canal):
            # No había nada que poner: la cancion que bajó vuelve a su volumen
            self._audio.slide_volume(canal, vol, 400)

    def _check_evento_inmediato(self):
        """
        Evento a hora fija con inicio inmediato (Jazler Force Clock): en
        automático, al llegar su hora se descarta lo que el plan tenía antes
        y lo que suena sale con fade. Si el evento se atrasó más de 15 min
        (p. ej. el sistema estuvo en manual) ya no corta nada.
        """
        if self._mode != "automatico" or self._pausado:
            return
        now = self._now()
        with self._programa_lock:
            items = list(self._programa)
            idx = next((i for i, it in enumerate(items)
                        if it.get("inmediato") and it.get("evento_hora")
                        and datetime.fromisoformat(it["evento_hora"]) <= now), None)
            if idx is None:
                # Aún no es la hora: los segundos previos la canción ya baja (la cuña entrará a la hora exacta)
                prox = next((it for it in items if it.get("inmediato") and it.get("evento_hora")), None)
                if prox:
                    falta = (datetime.fromisoformat(prox["evento_hora"]) - now).total_seconds()
                    if 0.05 < falta <= max(0.5, self._fade_corte) and prox["evento_hora"] not in self._evento_prefade:
                        self._evento_prefade.add(prox["evento_hora"])
                        if self._prefade_iniciar(falta):
                            log.info("[Engine] Evento inmediato: la cancion baja %.1f s y el evento entra a su hora", falta)
                return
            ev = items[idx]
            if datetime.fromisoformat(ev["evento_hora"]) < now - timedelta(minutes=15):
                items[idx] = {**ev, "inmediato": False}
                self._programa = deque(items)
                return
            # Ya no es "inmediato": si la cola del operador entra antes, no se vuelve a cortar
            self._programa = deque([{**ev, "inmediato": False}] + items[idx + 1:])
        log.info("[Engine] Evento '%s' a las %s: entra en punto", ev.get("evento"), ev["evento_hora"][11:16])
        self.bus.emit("evento_inmediato", {"evento": ev.get("evento"), "hora": ev["evento_hora"][11:16]})
        self._emit_programa()
        ya_bajo = ev["evento_hora"] in self._evento_prefade or bool(self._prefade)
        self._evento_prefade.discard(ev["evento_hora"])
        self._prefade = None
        if not ya_bajo:                            # el motor llegó tarde: no hubo fade previo, sale con fade normal
            log.info("[Engine] Evento inmediato: la cancion sale con fade de %.1f s", self._fade_corte)
            self._corte_fade = self._fade_corte
        try:
            self._advance()
        finally:
            self._corte_fade = None

    def _plan_hour(self, slot: str, minuto_actual: int, pautas: list[dict]):
        """Reparte las pautas de la hora en tandas (una vez por hora)."""
        if self._plan_slot != slot:
            plan = planificar_tandas(pautas, self._tandas_minutos)
            self._plan = {m: [p["pauta_id"] for p in lst] for m, lst in plan.items()}
            self._plan_slot = slot
            self._tandas_lanzadas = set()
            self._dur_cierre = {}
            self._pautas_fallidas = {f for f in self._pautas_fallidas if f[0] == slot}
            return
        if not self._plan:
            return              # hora sin tandas
        planificadas = {pid for ids in self._plan.values() for pid in ids}
        for p in pautas:
            if p["pauta_id"] in planificadas:
                continue
            futuras = [m for m in self._plan if m > minuto_actual]
            destino = min(futuras) if futuras else max(self._plan)
            self._plan[destino].append(p["pauta_id"])
            log.info("[Pautas] Pauta nueva %d → tanda :%02d", p["pauta_id"], destino)

    def _proxima_tanda(self) -> Optional[dict]:
        """Siguiente tanda de la hora con cuñas planificadas (para el Studio)."""
        minuto_actual = self._now().minute
        for m in sorted(self._plan):
            if m > minuto_actual and self._plan[m]:
                return {"minuto": m, "pautas": len(self._plan[m])}
        return None

    # ── Gestion de archivos diferida (segura durante reproduccion) ────────────

    def is_track_playing(self, audio_id: int) -> bool:
        """Retorna True si esa pista esta siendo reproducida ahora mismo."""
        return (self._current_track is not None and
                self._current_track.get("id") == audio_id and
                self._audio.is_active(self._active_canal))

    def defer_file_move(self, audio_id: int, new_path: str):
        """
        Encola un movimiento de archivo para ejecutarse cuando la pista termine.
        La API llama a este metodo si el archivo esta siendo reproducido.
        """
        self._deferred_moves.append({"audio_id": audio_id, "new_path": new_path})
        log.info("[Engine] Movimiento diferido registrado: id=%d -> %s", audio_id, new_path)

    def _process_deferred_moves(self):
        if not self._deferred_moves:
            return
        still_pending = []
        for move in self._deferred_moves:
            if not self.is_track_playing(move["audio_id"]):
                self.bus.emit("execute_deferred_move", move)
            else:
                still_pending.append(move)
        self._deferred_moves = still_pending

    # ── Watchdog ─────────────────────────────────────────────────────────────
    # El watchdog corre en su propio hilo: aqui solo se encola la recuperacion
    # para que la ejecute el loop del motor.

    def _on_watchdog_recovery(self, event: str, data: dict):
        self._cmds.put(("recover",))

    def _on_watchdog_stall(self, event: str, data: dict):
        self._cmds.put(("restart",))

    def _do_recover(self):
        """Canal pausado o atascado: intenta reanudar; si no puede, salta."""
        if self._pausado:
            return                                 # pausa del operador, no una falla
        log.warning("[Engine] Watchdog recovery (silencio): intentando reanudar...")
        if self._current_track and self._audio.play(self._active_canal):
            self.bus.emit("watchdog_recovered", {"method": "resume"})
            return
        if not self._advance():
            self._do_stop("watchdog_sin_siguiente")
        self.bus.emit("watchdog_recovered", {"method": "next_track"})

    def _do_restart(self):
        """Posicion congelada: recarga la pista actual desde cero o salta."""
        if self._pausado:
            return
        log.error("[Engine] Watchdog stall: reiniciando stream desde cero...")
        item = self._current_item
        self._finish_crossfade(force=True)
        self._audio.stop(self._active_canal)
        self._current_track = None
        if item and self._start({**item, "_restart": True}):
            self.bus.emit("watchdog_recovered", {"method": "stall_restart"})
            return
        if not self._advance():
            self._do_stop("watchdog_sin_siguiente")
        self.bus.emit("watchdog_recovered", {"method": "stall_skip"})

    def _watchdog_state(self) -> dict:
        """Estado que el Watchdog necesita para sus dos verificaciones."""
        return {
            "should_be_playing":    self._should_be_playing and not self._pausado,
            "is_playing":           self._audio.is_playing(self._active_canal),
            "current_track":        self._current_track,
            "mode":                 self._mode,
            "current_position_sec": self._audio.get_position_sec(self._active_canal),
        }

    # ── Helpers internos ─────────────────────────────────────────────────────

    def _on_track_started(self, track: dict, item: dict, duracion: float):
        origen     = item.get("origen", "manual")
        tipo_audio = track.get("tipo_audio") or TIPO_MUSICA
        tipo       = "pauta" if origen == "pauta" else ("voz" if tipo_audio == TIPO_VOZ else "musica")

        if tipo_audio == TIPO_MUSICA and tipo == "musica":
            self._recent_history.append({
                "audio_id":     track["id"],
                "tipo_audio":   TIPO_MUSICA,
                "artista":      track["artista"],
                "genero_vocal": track.get("genero_vocal"),
                "ts":           datetime.now().isoformat(),
            })
        self._log_emision(track, item, tipo=tipo)

        self.bus.emit("track_started", {
            "id":           track["id"],
            "audio_id":     track["id"],
            "titulo":       track.get("titulo", ""),
            "artista":      track.get("artista", ""),
            "duracion":     duracion,
            "duracion_seg": duracion,
            "posicion_seg": self._cue_range(item)[0],   # arranca en el cue de inicio
            "genero_vocal": track.get("genero_vocal") or "",
            "cat1":         track.get("subgenero") or "",
            "cat2":         track.get("categoria") or "",
            "tipo_audio":   tipo_audio,
            "origen":       origen,
            "pauta_id":     item.get("pauta_id"),
            "cliente":      item.get("cliente"),
            "tanda":        item.get("tanda"),
            "reloj":        item.get("reloj"),
            # ─ Cue points (6 marcadores Jazler) ───────────────────────────
            "cue_inicio":      track.get("cue_inicio")      or 0,
            "cue_intro":       track.get("cue_intro")       or track.get("intro") or 0,
            "cue_inicio_coro": track.get("cue_inicio_coro") or 0,
            "cue_final_coro":  track.get("cue_final_coro")  or 0,
            "cue_mezcla":      self._mix_point if self._mix_point != float("inf") else 0,
            "fade_in":         track.get("fade_in")         or 0,
            "fade_out":        track.get("fade_out")        or 0,
        })
        log.info(
            "[Engine] ▶ %s — %s  [%s, dur=%.1fs, mezcla=%.1fs]",
            track.get("artista", "?"), track.get("titulo", "?"),
            origen, duracion, self._mix_point if self._mix_point != float("inf") else 0,
        )

    def _log_emision(self, track: dict, item: dict, tipo: str):
        """Registra la emision en historial_emision y actualiza la rotacion."""
        try:
            conn = _db_conn(self._db)
            try:
                conn.execute(
                    "INSERT INTO historial_emision"
                    " (audio_id, titulo, artista, tipo, origen, pauta_id, slot, tanda)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (track["id"], track.get("titulo", ""), track.get("artista", ""),
                     tipo, item.get("origen", "manual"),
                     item.get("pauta_id"), item.get("slot"), item.get("tanda")),
                )
                conn.execute(
                    "UPDATE audios SET"
                    " ultima_reproduccion = NOW(),"
                    " veces_reproducido = COALESCE(veces_reproducido,0) + 1"
                    " WHERE id = ?",
                    (track["id"],),
                )
                if item.get("pauta_id"):
                    try:
                        # Al salir, salda UNA deuda pendiente de esa pauta (SQL que sirve igual en MariaDB y en SQLite)
                        deuda = conn.execute(
                            "SELECT MIN(id) AS id FROM pautas_deuda WHERE pauta_id = ? AND recuperada = 0",
                            (item["pauta_id"],),
                        ).fetchone()
                        if deuda and deuda["id"] is not None:
                            conn.execute(
                                "UPDATE pautas_deuda SET recuperada = 1, recuperada_en = CURRENT_TIMESTAMP WHERE id = ?",
                                (deuda["id"],),
                            )
                    except Exception:
                        pass
                conn.commit()
            finally:
                conn.close()

        except Exception as exc:
            log.error("[Engine] _log_emision: %s", exc)

    def _emit_error(self, audio_id, reason: str, msg: str, **extra):
        self.last_error = msg
        self.bus.emit("playback_error", {
            "audio_id": audio_id, "reason": reason, "msg": msg, **extra,
        })

    def _emit_queue(self):
        self.bus.emit("queue_updated", {"queue": list(self._playlist_queue)})

    def _fetch_track(self, audio_id: int) -> Optional[dict]:
        """Obtiene el registro completo del audio (solo si esta activo)."""
        try:
            conn = _db_conn(self._db)
            try:
                row = conn.execute(
                    "SELECT * FROM audios WHERE id = ? AND activo = 1", (audio_id,)
                ).fetchone()
            finally:
                conn.close()
            return dict(row) if row else None
        except Exception as exc:
            log.error("[Engine] _fetch_track(%s): %s", audio_id, exc)
            return None

    def _cfg(self, clave: str, default: str) -> str:
        snap = getattr(self, "_cfg_snap", None)
        if snap is not None:
            v = snap.get(clave)
            return v if v is not None else default
        conn = _db_conn(self._db)
        try:
            row = conn.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
            return row[0] if row else default
        finally:
            conn.close()

    def _update_cfg_db(self, clave: str, valor: str):
        try:
            conn = _db_conn(self._db)
            try:
                conn.execute(
                    "INSERT INTO config (clave, valor) VALUES (?, ?)"
                    " ON DUPLICATE KEY UPDATE valor = VALUES(valor)",
                    (clave, valor),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log.error("[Engine] _update_cfg_db: %s", exc)

    @property
    def status(self) -> dict:
        track = self._current_track
        item  = self._current_item or {}
        return {
            "version":           VERSION,       # del proceso del motor (ver backend/version.py)
            "backend":           BACKEND,
            "running":           self._running,
            "modo":              self._mode,
            "current_track":     {
                "id":      track["id"],
                "titulo":  track.get("titulo", ""),
                "artista": track.get("artista", ""),
                "origen":  item.get("origen", "manual"),
                "cliente": item.get("cliente"),
                "tipo_audio":   track.get("tipo_audio") or TIPO_MUSICA,
                "genero_vocal": track.get("genero_vocal"),
                "reloj":        item.get("reloj"),
                "nota":         item.get("nota") or "",     # memo para el locutor (playlists)
                # Marcas en segundos del archivo (como position_sec)
                "cue_inicio":   self._cue_range(item)[0] if item.get("track") else 0,
                "cue_intro":    float(track.get("cue_intro") or track.get("intro") or 0),
                "cue_outro":    float(track.get("cue_outro") or 0),      # dónde termina la voz
                "mezcla":       round(self._mix_point, 2) if self._mix_point != float("inf") else None,
                "fin":          round(self._hard_end, 2) if self._hard_end != float("inf") else None,
            } if track else None,
            "position_sec":      round(self._audio.get_position_sec(self._active_canal), 2) if track else 0,
            "duration_sec":      round(self._duration, 2) if track else 0,
            "queue":             list(self._playlist_queue),
            "queue_length":      len(self._playlist_queue),
            "pausado":           self._pausado,
            "pautas_pendientes": len(self._priority_queue),
            "tandas_minutos":    list(self._tandas_minutos),
            "tandas_cierre":     list(self._tandas_cierre),
            "fade_corte_seg":    self._fade_corte,
            "fade_corte_curva":  [round(c * 100) for c in self._fade_curva],
            # Modo manual: cuñas esperando a que el operador lance la tanda
            "tanda_en_espera":   (len(self._priority_queue)
                                  if self._mode == "manual" and not self._tanda_liberada else 0),
            "salidas":           dict(self._devices),
            "programa_items":    len(self._programa),
            "preescucha":        {
                "audio_id": self._cue_track.get("id"),
                "titulo":   self._cue_track.get("titulo", ""),
                "posicion": round(self._audio.get_position_sec(CANAL_CUE), 2),
                "duracion": round(self._audio.get_duration_sec(CANAL_CUE), 2),
            } if self._cue_track else None,
            "proxima_tanda":     self._proxima_tanda(),
            "crossfade_active":  self._xfade is not None,
            # Voice track al aire sobre la canción anterior (que sigue bajita)
            "voz_sobre":         {"audio_id": self._cama["track"].get("id"),
                                  "titulo": self._cama["track"].get("titulo", "")} if self._cama else None,
            # Tip montado sobre la canción (programado o sonando) y el que espera a la próxima canción
            "tip":               self._tip_info(self._tip),
            "tip_pendiente":     self._tip_info(self._tip_pend),
            "crossfade_seg":     self._crossfade_secs,
            "should_be_playing": self._should_be_playing,
            "detener_al_terminar": self._detener_al_terminar,
            "retenido":          self._retenido,
            "is_playing":        bool(track) and self._audio.is_playing(self._active_canal),
            "last_error":        self.last_error,
        }


# ── Singleton global ──────────────────────────────────────────────────────────
engine = AudioEngine()
