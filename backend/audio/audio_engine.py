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
import sqlite3
import threading
import time
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

_THIS_FILE = Path(__file__).resolve()

# Tipos de audio (audios.tipo_audio). Solo 'Musica' entra en la rotacion
# automatica; las cuñas salen unicamente por la programacion de pautas.
TIPO_MUSICA = "Musica"
TIPO_CUNA   = "Cuña"

# Normalizacion: la ganancia por audio (audios.ganancia_db) se aplica como
# volumen del canal. Se permite subir hasta +6 dB (volumen 2.0); el analisis
# ya limita la ganancia para que el pico no pase de -1 dBFS.
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


def _rubro(pauta: dict) -> str:
    return (pauta.get("rubro") or "").strip().upper()


def ordenar_tanda(pautas: list[dict]) -> list[dict]:
    """
    Orden de salida dentro de una tanda: respeta el orden recibido
    (prioridad primero) pero evita dos cuñas seguidas del mismo rubro.
    """
    pendientes, orden = list(pautas), []
    while pendientes:
        previo = _rubro(orden[-1]) if orden else ""
        idx = next((i for i, p in enumerate(pendientes)
                    if not previo or _rubro(p) != previo), 0)
        orden.append(pendientes.pop(idx))
    return orden


def planificar_tandas(pautas: list[dict], minutos: list[int]) -> dict[int, list[dict]]:
    """
    Reparte las pautas de una hora entre sus tandas. `pautas` llega ordenada
    por prioridad. Cada pauta va a la tanda con menos anunciantes de su mismo
    rubro y, a igualdad, a la menos cargada (y la mas temprana).
    """
    tandas: dict[int, list[dict]] = {m: [] for m in minutos}
    for p in pautas:
        rubro = _rubro(p)

        def costo(m: int):
            mismos = sum(1 for q in tandas[m] if rubro and _rubro(q) == rubro)
            return (mismos, len(tandas[m]), m)

        tandas[min(minutos, key=costo)].append(p)
    return {m: ordenar_tanda(lst) for m, lst in tandas.items()}


def _db_conn(db_path: Path = None) -> sqlite3.Connection:
    """
    Helper centralizado para abrir conexiones SQLite con:
      - WAL journal mode  → lecturas concurrentes sin bloquear escrituras
      - row_factory       → acceso por nombre de columna
      - foreign keys      → integridad referencial
      - check_same_thread → False para uso con threading del motor
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
# EventBus -- Pub/Sub interno
# ══════════════════════════════════════════════════════════════════════════════
class EventBus:
    """
    Bus de eventos pub/sub desacoplado.
    Permite que api_server.py se suscriba a eventos del motor
    sin importar directamente las clases del motor.
    """

    def __init__(self):
        self._listeners: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event: str, callback: Callable):
        with self._lock:
            self._listeners.setdefault(event, []).append(callback)

    def unsubscribe(self, event: str, callback: Callable):
        with self._lock:
            if event in self._listeners:
                self._listeners[event] = [
                    c for c in self._listeners[event] if c != callback
                ]

    def emit(self, event: str, payload: dict = None):
        payload = payload or {}
        with self._lock:
            callbacks = list(self._listeners.get(event, []))
        for cb in callbacks:
            try:
                cb(event, payload)
            except Exception as exc:
                log.error("[EventBus] Error en '%s': %s", event, exc)


# ══════════════════════════════════════════════════════════════════════════════
# BASS Backend -- Motor profesional via ctypes
# ══════════════════════════════════════════════════════════════════════════════
class BASSBackend:
    """
    Wrapper sobre bass.dll via ctypes.

    Cada 'canal logico' (0=A, 1=B, 2-7=efx) corresponde a un stream
    handle de BASS. Los streams se crean al cargar un archivo y se liberan
    al detener el canal o al cargar uno nuevo.
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

        self._handles: dict[int, int] = {}  # canal_idx -> BASS handle
        self._initialized = False

    def init(self):
        """
        Inicializa BASS. ONIX_AUDIO_DEVICE elige el dispositivo:
        -1 = predeterminado del sistema (default), 0 = "sin sonido"
        (los streams corren en tiempo real pero no salen por ninguna tarjeta).
        """
        device = int(os.getenv("ONIX_AUDIO_DEVICE", "-1"))
        ok = self._bass.BASS_Init(device, 44100, 0, None, None)
        if not ok:
            err = self._bass.BASS_ErrorGetCode()
            raise RuntimeError(
                f"BASS_Init fallo (codigo de error: {err}, dispositivo {device}). "
                f"Verifica que haya un dispositivo de audio disponible en el sistema."
            )
        self._initialized = True
        log.info("[BASS] Motor inicializado -- 44100 Hz, dispositivo %d", device)

    def load_stream(self, path: str, channel_idx: int) -> bool:
        """
        Crea un stream BASS desde un archivo de audio en disco.
        Libera automaticamente el stream anterior en ese canal si existe.
        """
        self._free_channel(channel_idx)

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
        if pos_bytes < 0:
            return False
        return bool(self._bass.BASS_ChannelSetPosition(handle, pos_bytes, BASS_POS_BYTE))

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
        if self._initialized:
            self._bass.BASS_Free()
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

    def __init__(self):
        self._ch: dict[int, dict] = {}

    def init(self): pass

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

    def load_stream(self, path: str, channel_idx: int) -> bool:
        self._ch[channel_idx] = {"dur": self._probe(path), "started": None, "offset": 0.0}
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

    def set_volume(self, channel_idx: int, vol: float): pass
    def slide_volume(self, channel_idx: int, vol: float, ms: int): pass

    def get_position_sec(self, channel_idx: int) -> float:
        ch = self._ch.get(channel_idx)
        if not ch or ch["started"] is None:
            return 0.0
        return min(ch["dur"], ch["offset"] + time.monotonic() - ch["started"])

    def set_position_sec(self, channel_idx: int, sec: float) -> bool:
        ch = self._ch.get(channel_idx)
        if not ch:
            return False
        ch["offset"] = max(0.0, min(float(sec), ch["dur"]))
        return True

    def is_playing(self, channel_idx: int) -> bool:
        ch = self._ch.get(channel_idx)
        return bool(ch and ch["started"] is not None
                    and self.get_position_sec(channel_idx) < ch["dur"])

    def is_active(self, channel_idx: int) -> bool:
        return self.is_playing(channel_idx)

    def get_duration_sec(self, channel_idx: int) -> float:
        ch = self._ch.get(channel_idx)
        return ch["dur"] if ch else 0.0

    def teardown(self): pass


# ══════════════════════════════════════════════════════════════════════════════
# Algoritmo de Rotacion
# ══════════════════════════════════════════════════════════════════════════════
class RotationAlgorithm:
    """
    Selecciona la siguiente pista musical aplicando reglas en cascada.
    0. Solo audios de tipo 'Musica' que no esten ligados a una pauta: las
       cuñas, jingles y demas tipos nunca salen por rotacion.
    1. Excluir artistas reproducidos en los ultimos N minutos.
    2. Bloquear la voz (genero_vocal) si se repite mas de M veces seguidas.
    3. Ordenar por ultima_reproduccion ASC (las menos escuchadas primero).
    """

    _BASE = (
        "a.activo=1"
        " AND a.tipo_audio = ?"
        " AND a.id NOT IN (SELECT audio_id FROM pautas WHERE audio_id IS NOT NULL)"
    )

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _cfg(self, conn, clave: str, default: str) -> str:
        row = conn.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
        return row["valor"] if row else default

    def select_next(self, recent_history: deque, exclude_ids=()) -> Optional[dict]:
        conn = _db_conn(self.db_path)
        try:
            ventana_min = int(self._cfg(conn, "ventana_artista_min", "60"))
            max_genero  = int(self._cfg(conn, "max_mismo_genero",     "2"))
            ventana_ini = datetime.now() - timedelta(minutes=ventana_min)
            history     = list(recent_history)

            artistas_bloq = {
                h["artista"] for h in history
                if datetime.fromisoformat(h["ts"]) >= ventana_ini
            }

            genero_bloq = None
            if max_genero > 0 and len(history) >= max_genero:
                generos = {h["genero_vocal"] for h in history[-max_genero:]}
                if len(generos) == 1:
                    genero_bloq = generos.pop()   # None si no estan clasificadas

            base_clauses = [self._BASE]
            base_params  = [TIPO_MUSICA]
            if exclude_ids:
                ph = ",".join("?" * len(exclude_ids))
                base_clauses.append(f"a.id NOT IN ({ph})")
                base_params.extend(exclude_ids)

            clauses, params = list(base_clauses), list(base_params)
            if artistas_bloq:
                ph = ",".join("?" * len(artistas_bloq))
                clauses.append(f"a.artista NOT IN ({ph})")
                params.extend(artistas_bloq)
            if genero_bloq:
                clauses.append("COALESCE(a.genero_vocal,'') != ?")
                params.append(genero_bloq)

            order = "a.ultima_reproduccion ASC NULLS FIRST"
            rows = conn.execute(
                f"SELECT a.* FROM audios a WHERE {' AND '.join(clauses)}"
                f" ORDER BY {order} LIMIT 10",
                params
            ).fetchall()

            if not rows:
                # Biblioteca pequeña: relajar reglas, solo evitar repetir artista seguido.
                ult = history[-1]["artista"] if history else None
                rows = conn.execute(
                    f"SELECT a.* FROM audios a WHERE {' AND '.join(base_clauses)}"
                    f" AND (? IS NULL OR a.artista != ?)"
                    f" ORDER BY {order} LIMIT 3",
                    base_params + [ult, ult]
                ).fetchall()

            if not rows:
                # Ultimo recurso: cualquier pista musical disponible.
                rows = conn.execute(
                    f"SELECT a.* FROM audios a WHERE {' AND '.join(base_clauses)}"
                    f" ORDER BY {order} LIMIT 1",
                    base_params
                ).fetchall()

            if not rows:
                return None

            return dict(random.choice(rows[:5]))
        finally:
            conn.close()


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
        fire_effect, set_mode, status
    """

    TICK               = 0.05   # segundos entre ciclos del loop
    PAUTAS_INTERVAL    = 5.0    # segundos entre consultas a la tabla pautas
    IDLE_RETRY         = 10.0   # espera antes de reintentar si no hay nada que sonar
    FAILED_TTL         = 600.0  # segundos que se evita un audio cuyo archivo fallo
    SPOT_FADE_IN       = 1.0    # salida de la musica al entrar una cuña
    SPOT_FADE_OUT      = 0.2    # empalme al terminar una cuña

    def __init__(self, db_path: Path = None, respaldo_dir: Path = None):
        self.bus = EventBus()

        self._audio = BASSBackend() if BACKEND == "bass" else MockBackend()

        self._db       = db_path or DB_PATH
        self._respaldo_dir = respaldo_dir or RESPALDO_DIR
        self._now      = datetime.now          # reloj de pared (las pruebas lo sustituyen)
        self._rotation = RotationAlgorithm(self._db)
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
        self._crossfade_secs   = 4.0
        self._master_volume    = 1.0
        self._effect_ducking   = False
        self._ducking_vol      = 0.25
        self._duck_until       = 0.0

        self._duration         = 0.0
        self._mix_point        = float("inf")
        self._hard_end         = float("inf")   # corte duro (cue_outro de la pauta)
        self._mix_fired        = False

        # Tandas de la hora en curso: {minuto: [pauta_id, ...]}
        self._tandas_minutos:  list[int] = [15, 45]
        self._plan_slot:       Optional[str] = None
        self._plan:            dict[int, list[int]] = {}
        self._tandas_lanzadas: set[int] = set()
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
        self._audio.init()
        self._load_config()
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

    def _load_config(self):
        try:
            self._crossfade_secs = max(0.0, float(self._cfg("crossfade_seg", "4")))
            modo = self._cfg("modo", "manual")
            self._mode = modo if modo in ("manual", "automatico") else "manual"
            self._ducking_vol = 10 ** (float(self._cfg("ducking_db", "-12")) / 20)
            minutos = parse_minutos(self._cfg("tandas_minutos", "15,45"))
            if minutos != self._tandas_minutos:
                self._tandas_minutos = minutos
                self._plan_slot = None   # replanificar la hora con las nuevas tandas
        except Exception as exc:
            log.warning("[Engine] Config no cargada: %s", exc)

    # ── Loop principal ────────────────────────────────────────────────────────

    def _run_loop(self):
        while self._running:
            try:
                self._drain_commands()
                self._finish_crossfade()
                self._restore_ducking()
                self._check_pautas()
                self._tick()
                self._process_deferred_moves()
            except Exception as exc:
                log.error("[Engine] Error en loop: %s", exc, exc_info=True)
            time.sleep(self.TICK)

    def _tick(self):
        """
        Vigila el canal activo: dispara la mezcla al llegar al punto de mezcla
        y releva la pista cuando termina. En modo automatico arranca solo.
        """
        if self._xfade:
            return

        track = self._current_track
        if track:
            if self._audio.is_active(self._active_canal):
                pos = self._audio.get_position_sec(self._active_canal)
                if pos < self._hard_end:
                    if not self._mix_fired and pos >= self._mix_point:
                        # Si no hay siguiente, la pista simplemente termina completa.
                        self._mix_fired = True
                        self._advance()
                    return

            # La pista termino sin relevo previo.
            self._audio.stop(self._active_canal)
            self._current_track = None
            self._current_item  = None
            self.bus.emit("track_ended", {"audio_id": track.get("id"), "completada": True})
            if not self._advance():
                self._should_be_playing = False
                self.bus.emit("playback_stopped", {"reason": "sin_siguiente"})
            return

        if self._mode == "automatico" and time.monotonic() >= self._idle_retry_at:
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

    def queue_track(self, audio_id: int) -> bool:
        track = self._fetch_track(audio_id)
        if not track:
            return False
        self._playlist_queue.append({
            "id": track["id"], "titulo": track["titulo"], "artista": track["artista"],
        })
        self._emit_queue()
        return True

    def clear_queue(self):
        self._playlist_queue.clear()
        self._emit_queue()

    def skip(self):
        self._cmds.put(("skip",))

    def stop_playback(self):
        self._cmds.put(("stop",))

    def fire_effect(self, audio_id: int, canal_offset: int = 0) -> bool:
        """Dispara un jingle o cuña en canal paralelo con auto-ducking."""
        track = self._fetch_track(audio_id)
        if not track:
            return False
        self._cmds.put(("effect", track, canal_offset))
        return True

    def set_mode(self, mode: str):
        if mode not in ("manual", "automatico"):
            raise ValueError(f"Modo invalido: {mode!r}")
        self._mode = mode
        self._idle_retry_at = 0.0
        self._update_cfg_db("modo", mode)
        self.bus.emit("mode_changed", {"modo": mode, "mode": mode})

    def _drain_commands(self):
        while True:
            try:
                cmd = self._cmds.get_nowait()
            except queue.Empty:
                return
            name = cmd[0]
            if name == "play":
                self._start(cmd[1])
            elif name == "play_next":
                if not self._current_track and not self._advance():
                    self._emit_error(None, "cola_vacia", "No hay nada en cola para reproducir")
            elif name == "skip":
                if not self._advance():
                    self._do_stop("skip_sin_siguiente")
            elif name == "stop":
                self._do_stop("manual")
            elif name == "effect":
                self._do_effect(cmd[1], cmd[2])
            elif name == "recover":
                self._do_recover()
            elif name == "restart":
                self._do_restart()

    # ── Seleccion y arranque de pistas ────────────────────────────────────────

    def _advance(self) -> bool:
        """Arranca el siguiente elemento disponible. False si no hay ninguno."""
        for _ in range(5):
            item = self._pick_next()
            if not item:
                return False
            if self._start(item):
                return True
        return False

    def _pick_next(self) -> Optional[dict]:
        """Prioridad: pautas de la hora > cola manual > rotacion automatica."""
        while self._priority_queue:
            pauta = self._priority_queue.popleft()
            track = self._fetch_track(pauta["audio_id"])
            if track:
                return {"track": track, "origen": "pauta", **pauta}
            self._pautas_fallidas.add((pauta["slot"], pauta["pauta_id"]))

        while self._playlist_queue:
            queued = self._playlist_queue.popleft()
            self._emit_queue()
            track = self._fetch_track(queued["id"])
            if track:
                return {"track": track, "origen": "cola"}

        if self._mode == "automatico":
            now = time.monotonic()
            self._failed_ids = {i: t for i, t in self._failed_ids.items()
                                if now - t < self.FAILED_TTL}
            try:
                track = self._rotation.select_next(
                    self._recent_history,
                    exclude_ids=tuple(i for i in self._failed_ids if isinstance(i, int)),
                )
            except Exception as exc:
                log.error("[Engine] Rotacion no disponible: %s", exc)
                track = None
            if track:
                return {"track": track, "origen": "auto"}
            return self._pick_respaldo()
        return None

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

        outgoing_active = (self._current_track is not None
                           and self._audio.is_active(self._active_canal))
        outgoing = self._active_canal
        canal    = (CANAL_B if outgoing == CANAL_A else CANAL_A) if outgoing_active else CANAL_A

        if not outgoing_active:
            self._audio.stop(CANAL_A)
            self._audio.stop(CANAL_B)

        if not self._audio.load_stream(path, canal):
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

        if outgoing_active:
            fade_in, fade_out = self._fade_times(self._current_item, item)
            prev = self._current_track
            self._audio.set_volume(canal, 0.0)
            if not self._audio.play(canal):
                self._audio.stop(canal)
                self._emit_error(track.get("id"), "error_play_backend",
                                 f"No se pudo reproducir: {track.get('titulo', '')}")
                return False
            self._audio.slide_volume(canal, target, int(fade_in * 1000))
            self._audio.slide_volume(outgoing, 0.0, int(fade_out * 1000))
            fade = max(fade_in, fade_out)
            self._xfade = {"canal": outgoing, "until": time.monotonic() + fade, "track": prev}
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

        if not item.get("_restart"):
            self._on_track_started(track, item, dur)
        return True

    def _finish_crossfade(self, force: bool = False):
        xf = self._xfade
        if not xf or (not force and time.monotonic() < xf["until"]):
            return
        self._xfade = None
        self._audio.stop(xf["canal"])
        self.bus.emit("track_ended", {"audio_id": xf["track"].get("id"), "completada": True})

    def _do_stop(self, reason: str):
        self._finish_crossfade(force=True)
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

    def _fade_times(self, prev_item: Optional[dict], item: dict) -> tuple[float, float]:
        """
        Duracion (s) de la entrada de `item` y de la salida de `prev_item`.
        Entre canciones vale el fade_in / fade_out de cada pista y, si no
        tienen, el crossfade general. Con cuñas de por medio se usan los
        empalmes cortos fijos (la musica no debe quedar debajo de la cuña).
        """
        if self._is_spot(item):
            return self.SPOT_FADE_IN, self.SPOT_FADE_IN
        if prev_item and self._is_spot(prev_item):
            return self.SPOT_FADE_OUT, self.SPOT_FADE_OUT
        fade_in  = float(item["track"].get("fade_in") or 0) or self._crossfade_secs
        fade_out = (float(prev_item["track"].get("fade_out") or 0) if prev_item else 0) \
            or self._crossfade_secs
        return fade_in, fade_out

    def _mark_failed(self, item: dict, path) -> None:
        """Evita reintentar enseguida un audio (o pauta) cuyo archivo fallo."""
        track = item["track"]
        self._failed_ids[track.get("id") if track.get("id") is not None else str(path)] = time.monotonic()
        if item.get("origen") == "pauta":
            self._pautas_fallidas.add((item.get("slot"), item.get("pauta_id")))

    @staticmethod
    def _gain_of(track: Optional[dict]) -> float:
        """Factor lineal de la ganancia de normalizacion guardada en el audio."""
        try:
            db = float((track or {}).get("ganancia_db") or 0)
        except (TypeError, ValueError):
            return 1.0
        return 10 ** (max(-30.0, min(MAX_BOOST_DB, db)) / 20)

    def _target_volume(self, track: Optional[dict] = None) -> float:
        track = track if track is not None else self._current_track
        duck  = self._ducking_vol if self._effect_ducking else 1.0
        return self._master_volume * duck * self._gain_of(track)

    # ── Cartuchera ────────────────────────────────────────────────────────────

    def _do_effect(self, track: dict, canal_offset: int):
        path  = self._path_of(track)
        canal = CANAL_EFX_BASE + (int(canal_offset) % 6)

        if not path or not Path(path).exists():
            self._emit_error(track.get("id"), "archivo_no_encontrado",
                             f"Efecto no encontrado: {track.get('titulo', '')}", path=str(path))
            return
        if not self._audio.load_stream(path, canal):
            self._emit_error(track.get("id"), "error_carga_backend",
                             f"No se pudo abrir el efecto: {track.get('titulo', '')}")
            return

        self._audio.set_volume(canal, self._master_volume * self._gain_of(track))
        self._audio.play(canal)

        dur = self._audio.get_duration_sec(canal) or float(track.get("duracion") or 5)
        self._duck_until = max(self._duck_until, time.monotonic() + dur + 0.3)
        if not self._effect_ducking:
            self._effect_ducking = True
            if self._current_track:
                self._audio.slide_volume(self._active_canal, self._target_volume(), 200)

        self._log_emision(track, {"origen": "manual"}, tipo="efecto")
        self.bus.emit("effect_fired", {
            "audio_id": track["id"], "titulo": track.get("titulo", ""), "canal": canal,
        })
        log.info("[Engine] Efecto: %s (canal %d)", track.get("titulo"), canal)

    def _restore_ducking(self):
        if self._effect_ducking and time.monotonic() >= self._duck_until:
            self._effect_ducking = False
            if self._current_track and not self._xfade:
                self._audio.slide_volume(self._active_canal, self._target_volume(), 400)

    # ── Programacion de pautas ────────────────────────────────────────────────

    def _check_pautas(self):
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
        """
        if time.monotonic() - self._last_pautas_check < self.PAUTAS_INTERVAL:
            return
        self._last_pautas_check = time.monotonic()

        now  = self._now()
        slot = now.strftime("%Y-%m-%d-%H")
        key  = f"{(now.weekday() + 1) % 7}-{now.hour}"
        hoy  = now.date().isoformat()

        perdidas = [p for p in self._priority_queue if p["slot"] != slot]
        if perdidas:
            self._priority_queue = deque(p for p in self._priority_queue if p["slot"] == slot)
            for p in perdidas:
                log.warning("[Pautas] No salio en su hora: %s (%s)", p["cliente"], p["slot"])
                self.bus.emit("pauta_missed", p)

        try:
            conn = _db_conn(self._db)
            try:
                rows = conn.execute(
                    """
                    SELECT p.id, p.cliente, p.audio_id, p.matriz, p.rubro,
                           p.menciones_diarias, p.cue_intro, p.cue_outro
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
                del_dia = dict(conn.execute(
                    "SELECT pauta_id, COUNT(*) FROM historial_emision"
                    " WHERE slot LIKE ? AND pauta_id IS NOT NULL GROUP BY pauta_id",
                    (hoy + "-%",),
                ).fetchall())
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
                de_la_hora[row["id"]] = {
                    "pauta_id": row["id"], "cliente": row["cliente"],
                    "audio_id": row["audio_id"], "rubro": row["rubro"] or "",
                    "menciones_diarias": row["menciones_diarias"],
                    "cue_intro": row["cue_intro"], "cue_outro": row["cue_outro"],
                    "slot": slot,
                }

        self._plan_hour(slot, now.minute, list(de_la_hora.values()))

        en_cola = {p["pauta_id"] for p in self._priority_queue}
        for minuto in sorted(self._plan):
            if minuto > now.minute:
                break
            lanzadas = []
            for pid in self._plan[minuto]:
                pauta = de_la_hora.get(pid)
                if (pauta is None or pid in emitidas or pid in en_cola
                        or (slot, pid) in self._pautas_fallidas):
                    continue
                tope = pauta["menciones_diarias"]
                if tope and del_dia.get(pid, 0) >= tope:
                    continue
                pauta = {**pauta, "tanda": minuto}
                self._priority_queue.append(pauta)
                en_cola.add(pid)
                lanzadas.append(pauta)
                log.info("[Pautas] Tanda :%02d → %s (pauta %d)", minuto, pauta["cliente"], pid)
                self.bus.emit("pauta_scheduled", pauta)
            if lanzadas:
                self._idle_retry_at = 0.0
                if minuto not in self._tandas_lanzadas:
                    self._tandas_lanzadas.add(minuto)
                    self.bus.emit("tanda_scheduled", {
                        "minuto": minuto, "slot": slot,
                        "pautas": [{"pauta_id": p["pauta_id"], "cliente": p["cliente"]}
                                   for p in lanzadas],
                    })

    def _plan_hour(self, slot: str, minuto_actual: int, pautas: list[dict]):
        """Reparte las pautas de la hora en tandas (una vez por hora)."""
        if self._plan_slot != slot:
            plan = planificar_tandas(pautas, self._tandas_minutos)
            self._plan = {m: [p["pauta_id"] for p in lst] for m, lst in plan.items()}
            self._plan_slot = slot
            self._tandas_lanzadas = set()
            self._pautas_fallidas = {f for f in self._pautas_fallidas if f[0] == slot}
            return
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
        log.warning("[Engine] Watchdog recovery (silencio): intentando reanudar...")
        if self._current_track and self._audio.play(self._active_canal):
            self.bus.emit("watchdog_recovered", {"method": "resume"})
            return
        if not self._advance():
            self._do_stop("watchdog_sin_siguiente")
        self.bus.emit("watchdog_recovered", {"method": "next_track"})

    def _do_restart(self):
        """Posicion congelada: recarga la pista actual desde cero o salta."""
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
            "should_be_playing":    self._should_be_playing,
            "is_playing":           self._audio.is_playing(self._active_canal),
            "current_track":        self._current_track,
            "mode":                 self._mode,
            "current_position_sec": self._audio.get_position_sec(self._active_canal),
        }

    # ── Helpers internos ─────────────────────────────────────────────────────

    def _on_track_started(self, track: dict, item: dict, duracion: float):
        origen     = item.get("origen", "manual")
        tipo_audio = track.get("tipo_audio") or TIPO_MUSICA
        tipo       = "pauta" if origen == "pauta" else "musica"

        if tipo_audio == TIPO_MUSICA and tipo == "musica":
            self._recent_history.append({
                "audio_id":     track["id"],
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
                    " (audio_id, titulo, artista, tipo, origen, pauta_id, slot)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (track["id"], track.get("titulo", ""), track.get("artista", ""),
                     tipo, item.get("origen", "manual"),
                     item.get("pauta_id"), item.get("slot")),
                )
                conn.execute(
                    "UPDATE audios SET"
                    " ultima_reproduccion = datetime('now','localtime'),"
                    " veces_reproducido = COALESCE(veces_reproducido,0) + 1"
                    " WHERE id = ?",
                    (track["id"],),
                )
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
                    " ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
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
            "backend":           BACKEND,
            "running":           self._running,
            "modo":              self._mode,
            "current_track":     {
                "id":      track["id"],
                "titulo":  track.get("titulo", ""),
                "artista": track.get("artista", ""),
                "origen":  item.get("origen", "manual"),
                "cliente": item.get("cliente"),
            } if track else None,
            "position_sec":      round(self._audio.get_position_sec(self._active_canal), 2) if track else 0,
            "duration_sec":      round(self._duration, 2) if track else 0,
            "queue":             list(self._playlist_queue),
            "queue_length":      len(self._playlist_queue),
            "pautas_pendientes": len(self._priority_queue),
            "tandas_minutos":    list(self._tandas_minutos),
            "proxima_tanda":     self._proxima_tanda(),
            "crossfade_active":  self._xfade is not None,
            "crossfade_seg":     self._crossfade_secs,
            "should_be_playing": self._should_be_playing,
            "is_playing":        bool(track) and self._audio.is_playing(self._active_canal),
            "last_error":        self.last_error,
        }


# ── Singleton global ──────────────────────────────────────────────────────────
engine = AudioEngine()
