"""
audio_engine.py — Onix FM Digital  v3.0  (BASS Edition)
================================================================================

Motor de audio profesional para emision 24/7.

BACKENDS DISPONIBLES (en orden de preferencia):
    1. BASS.dll  -- Motor profesional, gapless real, SYNC_END callback.
                   Descarga GRATUITA para uso no comercial:
                   https://www.un4seen.com/bass.html
                   · Windows: bass.dll      (en la carpeta del proyecto)
                   · Linux:   libbass.so    (misma carpeta o /usr/lib)
                   · macOS:   libbass.dylib (misma carpeta)
    2. pygame    -- Fallback robusto. Sin gapless real, pero funcional.
    3. mock      -- Simulacion sin hardware. Solo para desarrollo/pruebas.

WATCHDOG:
    Hilo independiente que verifica cada 2 segundos si el canal activo
    sigue sonando. Si detecta silencio inesperado durante mas de
    SILENCE_TOLERANCE segundos, emite 'watchdog_recovery' y el motor
    principal reintenta reproducir.

GAPLESS PLAYBACK (solo BASS):
    BASS permite registrar BASS_SYNC_END que se ejecuta en el momento
    exacto en que el stream esta por terminar (dentro del mixer, sin
    latencia extra de threading). El motor carga el siguiente archivo
    en memoria antes de que el actual termine -> cero silencio entre pistas.

COLUMNAS BD (fuente de verdad: init_db.py):
    id, titulo, artista, genero_vocal, energia, origen,
    tipo_audio, ruta_archivo, puntos_audio, duracion_seg,
    ultima_reproduccion, veces_reproducido, activo, notas
"""

import ctypes
import json
import logging
import platform
import sqlite3
import threading
import time
from collections import deque
from ctypes import POINTER, c_bool, c_char_p, c_double, c_float
from ctypes import c_int, c_longlong, c_ulong, c_void_p
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("AudioEngine")

# ── Ruta canonica de la BD ────────────────────────────────────────────────────
# Path(__file__).resolve() es robusto en Windows, Linux y macOS sin importar
# desde donde se lance el servidor.
_THIS_FILE = Path(__file__).resolve()
DB_PATH    = _THIS_FILE.parent.parent.parent / "radio_core.db"


def _db_conn(db_path: Path = None) -> sqlite3.Connection:
    """
    Helper centralizado para abrir conexiones SQLite con:
      - WAL journal mode  → lecturas concurrentes sin bloquear escrituras
      - row_factory       → acceso por nombre de columna
      - foreign keys      → integridad referencial
      - check_same_thread → False para uso con threading del motor

    WAL (Write-Ahead Logging) elimina los errores 'database is locked'
    cuando el motor esta sonando y el administrador guarda cambios en
    paralelo desde la API. Ambas conexiones operan sobre el mismo
    archivo .db sin bloquearse mutuamente.
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), check_same_thread=False)
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

# SYNC_END dispara el callback cuando el stream llega al final del buffer.
# SYNC_MIXTIME = ejecuta el callback dentro del mismo hilo del mixer,
# eliminando la latencia de un thread adicional.
BASS_SYNC_END        = 2
BASS_SYNC_MIXTIME    = 0x40000000
BASS_SYNC_ONETIME    = 0x80000000

# PRESCAN = escanea el archivo completo al cargar para obtener duracion exacta.
BASS_STREAM_PRESCAN  = 0x20000
BASS_SAMPLE_FLOAT    = 0x100

# Tipo del callback de sincronizacion:
# void CALLBACK SyncProc(HSYNC handle, DWORD channel, DWORD data, void *user)
SYNCPROC = ctypes.CFUNCTYPE(None, c_ulong, c_ulong, c_ulong, c_void_p)


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
            lib = (ctypes.WinDLL(path) if system == "Windows"
                   else ctypes.CDLL(path))
            log.info("[BASS] Libreria encontrada: %s", path)
            return path
        except OSError:
            continue
    return None


BASS_LIB_PATH = _detect_bass_lib()

if BASS_LIB_PATH:
    BACKEND = "bass"
else:
    try:
        import pygame
        BACKEND = "pygame"
        log.warning("[ENGINE] BASS no encontrado -> fallback a pygame.")
        log.warning("[ENGINE] Para audio profesional descarga BASS en:")
        log.warning("[ENGINE] https://www.un4seen.com/bass.html")
    except ImportError:
        BACKEND = "mock"
        log.warning("[ENGINE] Sin backend de audio. Modo mock (sin sonido).")


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
    al terminar la reproduccion o al cargar uno nuevo.
    """

    def __init__(self):
        if platform.system() == "Windows":
            self._bass = ctypes.WinDLL(BASS_LIB_PATH)
        else:
            self._bass = ctypes.CDLL(BASS_LIB_PATH)

        # Definir tipos de argumentos y retorno para cada funcion BASS.
        # Sin esto, ctypes puede truncar valores de 64-bit en sistemas modernos.
        b = self._bass

        b.BASS_Init.restype  = c_bool
        b.BASS_Init.argtypes = [c_int, c_ulong, c_ulong, c_void_p, c_void_p]

        b.BASS_Free.restype = c_bool

        b.BASS_StreamCreateFile.restype  = c_ulong
        b.BASS_StreamCreateFile.argtypes = [
            c_bool,     # mem: False = archivo en disco
            c_char_p,   # file: ruta codificada como bytes
            c_longlong, # offset en el archivo
            c_longlong, # longitud (0 = todo el archivo)
            c_ulong,    # flags
        ]

        b.BASS_StreamFree.restype  = c_bool
        b.BASS_StreamFree.argtypes = [c_ulong]

        b.BASS_ChannelPlay.restype  = c_bool
        b.BASS_ChannelPlay.argtypes = [c_ulong, c_bool]

        b.BASS_ChannelStop.restype  = c_bool
        b.BASS_ChannelStop.argtypes = [c_ulong]

        b.BASS_ChannelIsActive.restype  = c_ulong
        b.BASS_ChannelIsActive.argtypes = [c_ulong]

        b.BASS_ChannelSetAttribute.restype  = c_bool
        b.BASS_ChannelSetAttribute.argtypes = [c_ulong, c_ulong, c_float]

        b.BASS_ChannelGetAttribute.restype  = c_bool
        b.BASS_ChannelGetAttribute.argtypes = [c_ulong, c_ulong, POINTER(c_float)]

        # GetLength / GetPosition retornan QWORD (64-bit)
        b.BASS_ChannelGetLength.restype  = c_longlong
        b.BASS_ChannelGetLength.argtypes = [c_ulong, c_ulong]

        b.BASS_ChannelGetPosition.restype  = c_longlong
        b.BASS_ChannelGetPosition.argtypes = [c_ulong, c_ulong]

        b.BASS_ChannelBytes2Seconds.restype  = c_double
        b.BASS_ChannelBytes2Seconds.argtypes = [c_ulong, c_longlong]

        # SetSync: registra callback para sincronizacion de fin de pista
        b.BASS_ChannelSetSync.restype  = c_ulong
        b.BASS_ChannelSetSync.argtypes = [
            c_ulong,    # handle del canal
            c_ulong,    # tipo de sync
            c_longlong, # parametro (0 para SYNC_END)
            SYNCPROC,   # funcion callback
            c_void_p,   # datos de usuario (puede ser None)
        ]

        b.BASS_ChannelRemoveSync.restype  = c_bool
        b.BASS_ChannelRemoveSync.argtypes = [c_ulong, c_ulong]

        b.BASS_ErrorGetCode.restype = c_int

        # Diccionarios internos indexados por canal logico
        self._handles:   dict[int, int]      = {}  # canal_idx -> BASS handle
        self._syncs:     dict[int, int]      = {}  # canal_idx -> HSYNC handle
        # CRITICO: guardar referencia al SYNCPROC para que ctypes no lo libere
        self._sync_refs: dict[int, SYNCPROC] = {}

        self._initialized = False

    def init(self):
        """
        Inicializa BASS con el dispositivo de audio predeterminado del sistema.
        El -1 como primer argumento significa "usar el dispositivo por defecto".
        """
        ok = self._bass.BASS_Init(-1, 44100, 0, None, None)
        if not ok:
            err = self._bass.BASS_ErrorGetCode()
            raise RuntimeError(
                f"BASS_Init fallo (codigo de error: {err}).\n"
                f"Verifica que bass.dll este en la raiz del proyecto\n"
                f"y que haya un dispositivo de audio disponible en el sistema."
            )
        self._initialized = True
        log.info("[BASS] Motor inicializado correctamente -- 44100 Hz")

    def load_stream(self, path: str, channel_idx: int) -> bool:
        """
        Crea un stream BASS desde un archivo de audio en disco.
        Libera automaticamente el stream anterior en ese canal si existe.
        """
        self._free_channel(channel_idx)

        flags  = BASS_STREAM_PRESCAN | BASS_SAMPLE_FLOAT
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
                handle, BASS_ATTRIB_VOL, c_float(max(0.0, min(1.0, vol)))
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

    def get_duration_sec(self, channel_idx: int) -> float:
        handle = self._handles.get(channel_idx)
        if not handle:
            return 0.0
        len_bytes = self._bass.BASS_ChannelGetLength(handle, BASS_POS_BYTE)
        if len_bytes < 0:
            return 0.0
        return float(self._bass.BASS_ChannelBytes2Seconds(handle, len_bytes))

    def set_sync_end(self, channel_idx: int, callback: Callable):
        """
        Registra el callback de fin de pista en el mixer de BASS.
        Este mecanismo es la base del gapless playback: el callback
        se ejecuta milisegundos antes de que haya silencio, dando
        tiempo al motor de cargar el siguiente stream sin bache.
        """
        handle = self._handles.get(channel_idx)
        if not handle:
            return

        if channel_idx in self._syncs:
            self._bass.BASS_ChannelRemoveSync(handle, self._syncs[channel_idx])

        def _cb(hsync: int, hchannel: int, data: int, user) -> None:
            try:
                callback(channel_idx)
            except Exception as exc:
                log.error("[BASS] Error en sync callback canal %d: %s", channel_idx, exc)

        sync_fn = SYNCPROC(_cb)
        self._sync_refs[channel_idx] = sync_fn  # mantener viva la referencia

        sync_handle = self._bass.BASS_ChannelSetSync(
            handle,
            BASS_SYNC_END | BASS_SYNC_MIXTIME,
            0, sync_fn, None
        )
        self._syncs[channel_idx] = sync_handle

    def _free_channel(self, channel_idx: int):
        handle = self._handles.pop(channel_idx, None)
        if handle:
            self._bass.BASS_StreamFree(handle)
        self._syncs.pop(channel_idx, None)
        self._sync_refs.pop(channel_idx, None)

    def teardown(self):
        for idx in list(self._handles.keys()):
            self._free_channel(idx)
        if self._initialized:
            self._bass.BASS_Free()
        log.info("[BASS] Motor liberado.")


# ══════════════════════════════════════════════════════════════════════════════
# Pygame Backend -- Fallback robusto
# ══════════════════════════════════════════════════════════════════════════════
class PygameBackend:
    CHANNELS = 8

    def init(self):
        import pygame as _pg
        self._pg = _pg
        _pg.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=2048)
        _pg.mixer.init()
        _pg.mixer.set_num_channels(self.CHANNELS)
        self._sounds: dict[int, object] = {}
        log.info("[pygame] Mixer OK -- 44100 Hz, %d canales", self.CHANNELS)

    def load_stream(self, path: str, channel_idx: int) -> bool:
        try:
            self._sounds[channel_idx] = self._pg.mixer.Sound(str(path))
            return True
        except Exception as exc:
            log.error("[pygame] load_stream error '%s': %s", path, exc)
            return False

    def play(self, channel_idx: int, restart: bool = False) -> bool:
        sound = self._sounds.get(channel_idx)
        if not sound:
            return False
        ch = self._pg.mixer.Channel(channel_idx)
        ch.play(sound)
        return True

    def stop(self, channel_idx: int):
        self._pg.mixer.Channel(channel_idx).stop()
        self._sounds.pop(channel_idx, None)

    def set_volume(self, channel_idx: int, vol: float):
        self._pg.mixer.Channel(channel_idx).set_volume(max(0.0, min(1.0, vol)))

    def is_playing(self, channel_idx: int) -> bool:
        return bool(self._pg.mixer.Channel(channel_idx).get_busy())

    def is_active(self, channel_idx: int) -> bool:
        return self.is_playing(channel_idx)

    def get_position_sec(self, channel_idx: int) -> float:
        return 0.0  # pygame no expone posicion en segundos directamente

    def get_duration_sec(self, channel_idx: int) -> float:
        sound = self._sounds.get(channel_idx)
        return sound.get_length() if sound else 0.0

    def set_sync_end(self, channel_idx: int, callback: Callable):
        pass  # pygame no tiene sync nativo -- se usa polling en _tick()

    def teardown(self):
        self._pg.mixer.quit()


# ══════════════════════════════════════════════════════════════════════════════
# Mock Backend -- Solo para desarrollo sin hardware de audio
# ══════════════════════════════════════════════════════════════════════════════
class MockBackend:
    def __init__(self):
        self._timers:   dict[int, threading.Timer] = {}
        self._playing:  dict[int, bool]            = {}
        self._end_cbs:  dict[int, Callable]        = {}
        self._durations: dict[int, float]          = {}

    def init(self): pass

    def load_stream(self, path: str, channel_idx: int) -> bool:
        self._durations[channel_idx] = 10.0
        return True

    def play(self, channel_idx: int, restart: bool = False) -> bool:
        self._playing[channel_idx] = True
        dur = self._durations.get(channel_idx, 10.0)
        def _done():
            self._playing[channel_idx] = False
            cb = self._end_cbs.get(channel_idx)
            if cb:
                cb(channel_idx)
        t = threading.Timer(dur, _done)
        t.daemon = True
        t.start()
        self._timers[channel_idx] = t
        log.info("[Mock] Canal %d activo por %.1fs", channel_idx, dur)
        return True

    def stop(self, channel_idx: int):
        t = self._timers.pop(channel_idx, None)
        if t:
            t.cancel()
        self._playing[channel_idx] = False

    def set_volume(self, channel_idx: int, vol: float): pass
    def is_playing(self, channel_idx: int) -> bool: return self._playing.get(channel_idx, False)
    def is_active(self, channel_idx: int) -> bool:  return self.is_playing(channel_idx)
    def get_position_sec(self, channel_idx: int) -> float: return 0.0
    def get_duration_sec(self, channel_idx: int) -> float: return self._durations.get(channel_idx, 0.0)

    def set_sync_end(self, channel_idx: int, callback: Callable):
        self._end_cbs[channel_idx] = callback

    def teardown(self): pass


# ══════════════════════════════════════════════════════════════════════════════
# Algoritmo de Rotacion
# ══════════════════════════════════════════════════════════════════════════════
class RotationAlgorithm:
    """
    Selecciona la siguiente pista aplicando reglas en cascada.
    1. Excluir artistas reproducidos en los ultimos N minutos.
    2. Bloquear genero vocal si se repite mas de M veces consecutivas.
    3. Opcionalmente forzar origen Nacional.
    4. Ordenar por ultima_reproduccion ASC (menos escuchadas primero).
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _conn(self):
        """Usa el helper centralizado con WAL para evitar locks concurrentes."""
        return _db_conn(self.db_path)

    def _cfg(self, conn, clave: str, default: str) -> str:
        row = conn.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
        return row["valor"] if row else default

    def select_next(
        self,
        recent_history: deque,
        forzar_nacional: bool = False,
        energia_target: Optional[int] = None,
    ) -> Optional[dict]:
        conn = self._conn()
        try:
            ventana_min = int(self._cfg(conn, "ventana_artista_min", "60"))
            max_genero  = int(self._cfg(conn, "max_mismo_genero",     "2"))
            ventana_ini = datetime.now() - timedelta(minutes=ventana_min)

            artistas_bloq = {
                h["artista"] for h in recent_history
                if datetime.fromisoformat(h["ts"]) >= ventana_ini
            }

            genero_bloq = None
            if len(recent_history) >= max_genero:
                ultimos = list(recent_history)[-max_genero:]
                generos = [h["genero_vocal"] for h in ultimos]
                if len(set(generos)) == 1:
                    genero_bloq = generos[0]

            clauses: list[str] = ["a.activo=1", "a.tipo_audio='Musica'"]
            params:  list      = []

            if artistas_bloq:
                ph = ",".join("?" * len(artistas_bloq))
                clauses.append(f"a.artista NOT IN ({ph})")
                params.extend(artistas_bloq)
            if genero_bloq:
                clauses.append("a.genero_vocal != ?")
                params.append(genero_bloq)
            if forzar_nacional:
                clauses.append("a.origen='Nacional'")

            where = " AND ".join(clauses)
            order = "a.ultima_reproduccion ASC NULLS FIRST"
            if energia_target:
                order = f"ABS(a.energia - {int(energia_target)}) ASC, {order}"

            rows = conn.execute(
                f"SELECT a.* FROM audios a WHERE {where} ORDER BY {order} LIMIT 10",
                params
            ).fetchall()

            if not rows:
                log.warning("[Rotation] Sin candidatos con reglas completas -- relajando.")
                ult = list(recent_history)[-1]["artista"] if recent_history else None
                rows = conn.execute(
                    "SELECT * FROM audios WHERE activo=1 AND tipo_audio='Musica'"
                    " AND (? IS NULL OR artista!=?)"
                    " ORDER BY ultima_reproduccion ASC NULLS FIRST LIMIT 1",
                    (ult, ult)
                ).fetchall()

            if not rows:
                log.error("[Rotation] Biblioteca completamente vacia.")
                return None

            import random
            return dict(random.choice(rows[:5] if len(rows) >= 5 else rows))
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

        if delta >= self.MIN_POSITION_DELTA:
            # Posicion avanzando con normalidad → reiniciar contador de stall
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
    """

    def __init__(self):
        self.bus = EventBus()

        if BACKEND == "bass":
            self._audio = BASSBackend()
        elif BACKEND == "pygame":
            self._audio = PygameBackend()
        else:
            self._audio = MockBackend()

        self._db       = DB_PATH
        self._rotation = RotationAlgorithm(self._db)
        self._watchdog = Watchdog(self.bus, self._watchdog_state)

        self._mode             = "manual"
        self._active_canal     = CANAL_A
        self._current_track:   Optional[dict] = None
        self._playlist_queue:  deque = deque()
        self._recent_history:  deque = deque(maxlen=50)

        self._crossfade_active   = False
        self._crossfade_secs     = 4.0
        self._crossfade_step_ms  = 80   # pasos mas finos que en pygame
        self._master_volume      = 1.0
        self._effect_ducking     = False
        self._ducking_vol        = 0.3

        self._running            = False
        self._thread:            Optional[threading.Thread] = None
        self._lock               = threading.Lock()
        self._should_be_playing  = False

        # Cola de movimientos fisicos de archivos diferidos hasta fin de pista
        self._deferred_moves: list[dict] = []

        log.info("[Engine] Backend seleccionado: %s", BACKEND)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
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

        log.info("[Engine] Motor de audio iniciado.")

    def stop(self):
        self._running = False
        self._watchdog.stop()
        if self._thread:
            self._thread.join(timeout=5)
        self._audio.teardown()
        self.bus.emit("engine_stopped", {})
        log.info("[Engine] Motor detenido.")

    def _load_config(self):
        try:
            conn = _db_conn(self._db)
            for clave, attr, cast in (
                ("crossfade_seg", "_crossfade_secs", float),
                ("modo",          "_mode",           str),
            ):
                row = conn.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
                if row:
                    setattr(self, attr, cast(row[0]))
            row = conn.execute("SELECT valor FROM config WHERE clave='ducking_db'").fetchone()
            if row:
                self._ducking_vol = 10 ** (float(row[0]) / 20)
            conn.close()
        except Exception as exc:
            log.warning("[Engine] Config no cargada: %s", exc)

    # ── Loop principal ────────────────────────────────────────────────────────

    def _run_loop(self):
        while self._running:
            try:
                self._tick()
                self._process_deferred_moves()
            except Exception as exc:
                log.error("[Engine] Error en loop: %s", exc, exc_info=True)
            time.sleep(0.25)

    def _tick(self):
        """
        Con BASS: el gapless lo maneja SYNC_END. El tick solo maneja casos
        de recuperacion que BASS no cubra.
        Con pygame/mock: polling de posicion para detectar el punto de crossfade.
        Usa cue_mezcla (o outro como fallback) en lugar del obsoleto JSON puntos_audio.
        """
        if not self._current_track or self._crossfade_active:
            return

        if BACKEND in ("pygame", "mock"):
            track    = self._current_track
            duracion = track.get("duracion_seg") or track.get("duracion") or 0

            # Prioridad: cue_mezcla > outro > (duracion - crossfade_secs)
            mix_point = (
                track.get("cue_mezcla")
                or track.get("outro")
                or (duracion - self._crossfade_secs if duracion > 0 else 0)
            )
            pos_s = self._audio.get_position_sec(self._active_canal)

            if duracion > 0 and pos_s > 0 and pos_s >= mix_point:
                self._prepare_and_crossfade()

    # ── Reproduccion ─────────────────────────────────────────────────────────

    def play_track(self, audio_id: int) -> bool:
        track = self._fetch_track(audio_id)
        if not track:
            return False
        with self._lock:
            if self._current_track and self._audio.is_active(self._active_canal):
                self._crossfade_to(track)
            else:
                self._play_direct(track, CANAL_A)
        return True

    def _play_direct(self, track: dict, canal: int) -> bool:
        """
        Verifica existencia fisica del archivo antes de intentar cargarlo.
        Acepta tanto 'ruta_archivo' (columna legada) como 'archivo_path'
        (columna nueva de la Biblioteca Musical) para compatibilidad total.
        """
        # Compatibilidad: tabla nueva usa archivo_path, tabla legada usa ruta_archivo
        path = track.get("ruta_archivo") or track.get("archivo_path") or ""

        if not path or not Path(path).exists():
            log.error("[Engine] Archivo no encontrado: '%s' (id=%s)", path, track.get("id"))
            self.bus.emit("playback_error", {
                "audio_id": track.get("id"),
                "reason":   "archivo_no_encontrado",
                "path":     str(path),
            })
            return False

        ok = self._audio.load_stream(path, canal)
        if not ok:
            self.bus.emit("playback_error", {
                "audio_id": track.get("id"),
                "reason":   "error_carga_backend",
            })
            return False

        if BACKEND == "bass":
            self._audio.set_sync_end(canal, self._on_bass_track_ended)

        self._audio.play(canal)
        self._audio.set_volume(canal, self._master_volume)

        self._active_canal      = canal
        self._current_track     = track
        self._should_be_playing = True

        self._on_track_started(track)
        return True

    def _on_bass_track_ended(self, canal: int):
        """
        Callback de SYNC_END. Se ejecuta dentro del mixer de BASS, sin
        latencia de threading. Desencadena el crossfade al siguiente track.
        """
        if self._running and not self._crossfade_active:
            log.debug("[BASS] SYNC_END en canal %d", canal)
            self._prepare_and_crossfade()

    def _crossfade_to(self, next_track: dict):
        outgoing = self._active_canal
        incoming = CANAL_B if outgoing == CANAL_A else CANAL_A
        from_id  = self._current_track.get("id") if self._current_track else None

        self.bus.emit("crossfade_start", {
            "from_id": from_id,
            "to_id":   next_track.get("id"),
            "duracion_crossfade": self._crossfade_secs,
        })
        self._crossfade_active = True

        def _do():
            next_path = next_track.get("ruta_archivo", "")
            if not next_path or not Path(next_path).exists():
                log.error("[Engine] Crossfade: destino no encontrado '%s'", next_path)
                self._crossfade_active = False
                self.bus.emit("playback_error", {
                    "audio_id": next_track.get("id"),
                    "reason":   "archivo_no_encontrado_crossfade",
                })
                return

            ok = self._audio.load_stream(next_path, incoming)
            if not ok:
                self._crossfade_active = False
                self.bus.emit("playback_error", {
                    "audio_id": next_track.get("id"),
                    "reason":   "error_carga_crossfade",
                })
                return

            self._audio.set_volume(incoming, 0.0)
            self._audio.play(incoming)

            if BACKEND == "bass":
                self._audio.set_sync_end(incoming, self._on_bass_track_ended)

            steps    = max(1, int(self._crossfade_secs * 1000 / self._crossfade_step_ms))
            step_vol = 1.0 / steps

            for i in range(steps + 1):
                if not self._running:
                    break
                fi = i * step_vol
                self._audio.set_volume(incoming, fi * self._master_volume)
                self._audio.set_volume(outgoing, (1.0 - fi) * self._master_volume)
                time.sleep(self._crossfade_step_ms / 1000.0)

            self._audio.stop(outgoing)

            if from_id:
                self.bus.emit("track_ended", {"audio_id": from_id, "completada": True})
                self._update_last_played(from_id)

            self._active_canal      = incoming
            self._current_track     = next_track
            self._crossfade_active  = False
            self._should_be_playing = True
            self._on_track_started(next_track)
            self._process_deferred_moves()

        threading.Thread(target=_do, name="Crossfade", daemon=True).start()

    def _prepare_and_crossfade(self):
        if self._crossfade_active:
            return
        nxt = None
        if self._playlist_queue and self._mode == "manual":
            nxt = self._fetch_track(self._playlist_queue.popleft())
        elif self._mode == "automatico":
            nxt = self._rotation.select_next(
                self._recent_history,
                forzar_nacional=self._get_cfg_bool("forzar_nacional"),
            )
        if nxt:
            self._crossfade_to(nxt)
        else:
            self._should_be_playing = False

    # ── Cartuchera ────────────────────────────────────────────────────────────

    def fire_effect(self, audio_id: int, canal_offset: int = 0) -> bool:
        """
        Dispara un jingle o cuña en canal paralelo con auto-ducking.
        Usa ruta_archivo (NO archivo_path) para localizar el archivo.
        """
        track = self._fetch_track(audio_id)
        if not track:
            return False

        path  = track.get("ruta_archivo", "")
        canal = CANAL_EFX_BASE + (canal_offset % 6)

        if not path or not Path(path).exists():
            log.error("[Engine] fire_effect: no existe '%s'", path)
            return False

        ok = self._audio.load_stream(path, canal)
        if not ok:
            return False

        if not self._effect_ducking:
            self._effect_ducking = True
            self._audio.set_volume(self._active_canal, self._ducking_vol)
            dur = track.get("duracion_seg", 5)
            def _restore():
                time.sleep(dur + 0.5)
                self._effect_ducking = False
                self._audio.set_volume(self._active_canal, self._master_volume)
            threading.Thread(target=_restore, daemon=True).start()

        self._audio.play(canal)
        self.bus.emit("effect_fired", {"audio_id": audio_id, "canal": canal})
        log.info("[Engine] Efecto: %s (canal %d)", track["titulo"], canal)
        return True

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

    def _on_watchdog_recovery(self, event: str, data: dict):
        """
        Responde a FALLO 1 (silencio total):
        Intenta reanudar el stream actual, y si no puede, salta a la siguiente pista.
        """
        log.warning("[Engine] Watchdog recovery (silencio): intentando reanudar...")
        with self._lock:
            if self._current_track and BACKEND == "bass":
                if self._audio.play(self._active_canal):
                    self._should_be_playing = True
                    self.bus.emit("watchdog_recovered", {"method": "resume"})
                    log.info("[Engine] Reanudacion exitosa por watchdog.")
                    return
            self._prepare_and_crossfade()
            self.bus.emit("watchdog_recovered", {"method": "next_track"})

    def _on_watchdog_stall(self, event: str, data: dict):
        """
        Responde a FALLO 2 (stall de posicion):
        El stream esta 'reproduciendo' pero la posicion esta congelada.
        La unica solucion fiable es recargar el stream desde cero.
        Si el track actual existe, lo vuelve a cargar desde el principio;
        si no, salta a la siguiente pista.
        """
        log.error("[Engine] Watchdog stall: reiniciando stream desde cero...")
        with self._lock:
            track = self._current_track
            if not track:
                self._prepare_and_crossfade()
                self.bus.emit("watchdog_recovered", {"method": "stall_next_track"})
                return

            # Detener el canal sin crossfade (el stream esta colgado)
            self._audio.stop(self._active_canal)

            # Recargar el stream desde el principio del archivo
            path = track.get("ruta_archivo", "")
            if path and Path(path).exists():
                ok = self._audio.load_stream(path, self._active_canal)
                if ok:
                    if BACKEND == "bass":
                        self._audio.set_sync_end(self._active_canal, self._on_bass_track_ended)
                    self._audio.play(self._active_canal)
                    self._audio.set_volume(self._active_canal, self._master_volume)
                    self._should_be_playing = True
                    log.info("[Engine] Stream reiniciado por stall: %s", track.get("titulo"))
                    self.bus.emit("watchdog_recovered", {"method": "stall_restart"})
                    return

            # Si no se pudo recargar, saltar a la siguiente pista
            self._prepare_and_crossfade()
            self.bus.emit("watchdog_recovered", {"method": "stall_skip"})

    def _watchdog_state(self) -> dict:
        """
        Expone el estado que el Watchdog necesita para sus dos verificaciones.
        current_position_sec es critico para la deteccion de stall:
        si el backend no soporta posicion (mock), devuelve None y el
        Watchdog desactiva la verificacion de stall automaticamente.
        """
        return {
            "should_be_playing":   self._should_be_playing,
            "is_playing":          self._audio.is_playing(self._active_canal),
            "current_track":       self._current_track,
            "mode":                self._mode,
            "current_position_sec": self._audio.get_position_sec(self._active_canal),
        }

    # ── Control publico ───────────────────────────────────────────────────────

    def set_mode(self, mode: str):
        assert mode in ("manual", "automatico")
        self._mode = mode
        self._update_cfg_db("modo", mode)
        self.bus.emit("mode_changed", {"modo": mode})

    def queue_track(self, audio_id: int):
        self._playlist_queue.append(audio_id)

    def stop_playback(self):
        self._audio.stop(self._active_canal)
        self._should_be_playing = False
        self._current_track     = None

    # ── Helpers internos ─────────────────────────────────────────────────────

    def _on_track_started(self, track: dict):
        self._recent_history.append({
            "audio_id":    track["id"],
            "artista":     track["artista"],
            "genero_vocal":track.get("genero_vocal", "Instrumental"),
            "ts":          datetime.now().isoformat(),
        })
        # Duracion: puede venir como duracion_seg (legado) o duracion (nueva BD)
        duracion = track.get("duracion_seg") or track.get("duracion") or 0

        self.bus.emit("track_started", {
            "id":          track["id"],
            "audio_id":    track["id"],
            "titulo":      track.get("titulo", ""),
            "artista":     track.get("artista", ""),
            "duracion":    duracion,
            "duracion_seg":duracion,
            "genero_vocal":track.get("genero_vocal", "Instrumental"),
            "energia":     track.get("energia", 3),
            "origen":      track.get("origen", "Nacional"),
            "tipo_audio":  track.get("tipo_audio", "Musica"),
            # ─ Cue points (6 marcadores Jazler) ───────────────────────────
            "cue_inicio":      track.get("cue_inicio")      or track.get("intro")  or 0,
            "cue_intro":       track.get("cue_intro")       or track.get("intro")  or 0,
            "cue_inicio_coro": track.get("cue_inicio_coro") or 0,
            "cue_final_coro":  track.get("cue_final_coro")  or 0,
            "cue_mezcla":      track.get("cue_mezcla")      or track.get("outro") or 0,
            "fade_in":         track.get("fade_in")         or 0,
            "fade_out":        track.get("fade_out")        or 0,
        })
        log.info(
            "[Engine] ▶ %s — %s  [intro=%.1fs  mezcla=%.1fs]",
            track.get("artista", "?"), track.get("titulo", "?"),
            track.get("cue_intro") or track.get("intro") or 0,
            track.get("cue_mezcla") or track.get("outro") or 0,
        )

    def _fetch_track(self, audio_id: int) -> Optional[dict]:
        """
        Obtiene el track completo de la DB incluyendo todos los cue points.
        Usa columnas de ambas tablas (ruta_archivo legada + archivo_path nueva)
        para que el motor funcione con cualquier registro.
        """
        try:
            conn = _db_conn(self._db)
            row = conn.execute(
                """
                SELECT id, titulo, artista, album, duracion, archivo_path,
                       COALESCE(ruta_archivo, archivo_path) AS ruta_archivo,
                       subgenero AS cat1, categoria AS cat2, cat3,
                       genero_vocal, bpm, activo,
                       intro, outro, hook,
                       cue_inicio, cue_intro, cue_inicio_coro,
                       cue_final_coro, cue_mezcla, fade_in, fade_out
                FROM audios WHERE id = ? AND activo = 1
                """,
                (audio_id,)
            ).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as exc:
            log.error("[Engine] _fetch_track(%d): %s", audio_id, exc)
            return None

    def _update_last_played(self, audio_id: Optional[int]):
        if not audio_id:
            return
        try:
            conn = _db_conn(self._db)
            conn.execute(
                "UPDATE audios SET "
                "ultima_reproduccion=datetime('now','localtime'), "
                "veces_reproducido=COALESCE(veces_reproducido,0)+1 WHERE id=?",
                (audio_id,)
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            log.error("[Engine] _update_last_played: %s", exc)

    def _get_cfg_bool(self, clave: str) -> bool:
        try:
            conn = _db_conn(self._db)
            row  = conn.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
            conn.close()
            return bool(row and row[0].lower() in ("true", "1", "yes", "si"))
        except Exception:
            return False

    def _update_cfg_db(self, clave: str, valor: str):
        try:
            conn = _db_conn(self._db)
            conn.execute("UPDATE config SET valor=? WHERE clave=?", (valor, clave))
            conn.commit()
            conn.close()
        except Exception as exc:
            log.error("[Engine] _update_cfg_db: %s", exc)

    @property
    def status(self) -> dict:
        return {
            "backend":           BACKEND,
            "modo":              self._mode,
            "current_track":     self._current_track,
            "queue_length":      len(self._playlist_queue),
            "crossfade_active":  self._crossfade_active,
            "should_be_playing": self._should_be_playing,
            "is_playing":        self._audio.is_playing(self._active_canal),
        }


# ── Singleton global ──────────────────────────────────────────────────────────
engine = AudioEngine()
