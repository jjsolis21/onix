"""
engine_client.py — Ónix FM
Cliente del motor de audio cuando corre como proceso aparte
(backend/audio/engine_service.py). Expone la misma interfaz que AudioEngine,
así api_server.py no distingue entre motor interno y remoto.

Si el motor no está disponible, los comandos fallan con un mensaje claro y
/api/v1/status lo informa; el cliente reintenta la conexión cada 2 s.
Detener la API NO detiene el motor (ese es el punto).
"""

import itertools
import json
import logging
import socket
import threading
from typing import Optional

from backend.audio.eventbus import EventBus

log = logging.getLogger("EngineClient")


class MotorNoDisponible(ConnectionError):
    pass


class RemoteEngine:
    RECONNECT_SECS = 2.0

    def __init__(self, host: str = "127.0.0.1", port: int = 8766, timeout: float = 3.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.bus = EventBus()
        self.last_error: Optional[str] = None
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._pending_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._running = False
        self._conectado = threading.Event()
        self._parar = threading.Event()

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def start(self):
        """Conecta en segundo plano (no falla si el motor aún no arrancó)."""
        self._running = True
        self._parar.clear()
        threading.Thread(target=self._connect_loop, name="EngineLink", daemon=True).start()

    def stop(self):
        """Cierra la conexión. El motor remoto sigue sonando."""
        self._running = False
        self._parar.set()
        self._close()

    def wait_connected(self, timeout: float) -> bool:
        return self._conectado.wait(timeout)

    @property
    def connected(self) -> bool:
        return self._conectado.is_set()

    def _connect_loop(self):
        avisado = False
        while self._running:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            except OSError as exc:
                self.last_error = f"Motor de audio no disponible en {self.host}:{self.port}"
                if not avisado:
                    log.warning("[Motor remoto] %s (%s); reintentando...", self.last_error, exc)
                    avisado = True
                self._parar.wait(self.RECONNECT_SECS)
                continue
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock = sock
            self.last_error = None
            avisado = False
            self._conectado.set()
            log.info("[Motor remoto] Conectado a %s:%d", self.host, self.port)
            self.bus.emit("engine_connected", {"host": self.host, "port": self.port})
            # Por si la configuración cambió mientras no había conexión
            threading.Thread(target=self.reload_config, daemon=True).start()
            self._read_loop(sock)        # vuelve al cortarse la conexión
            self._conectado.clear()
            self._fail_pending()
            if self._running:
                self.last_error = "Se perdió la conexión con el motor de audio"
                log.error("[Motor remoto] %s; reintentando...", self.last_error)
                self.bus.emit("engine_stopped", {"reason": "motor_desconectado"})
                self._parar.wait(self.RECONNECT_SECS)

    def _read_loop(self, sock: socket.socket):
        try:
            with sock.makefile("r", encoding="utf-8") as reader:
                for line in reader:
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if "event" in msg:
                        self.bus.emit(msg["event"], msg.get("data") or {})
                        continue
                    with self._pending_lock:
                        slot = self._pending.get(msg.get("id"))
                    if slot is not None:
                        slot["resp"] = msg
                        slot["evt"].set()
        except (OSError, ValueError):
            pass
        finally:
            self._close()

    def _close(self):
        sock, self._sock = self._sock, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _fail_pending(self):
        with self._pending_lock:
            for slot in self._pending.values():
                slot["evt"].set()

    # ── Llamadas ─────────────────────────────────────────────────────────────

    def _call(self, method: str, *args):
        sock = self._sock
        if sock is None or not self.connected:
            raise MotorNoDisponible(self.last_error or "Motor de audio no conectado")
        rid = next(self._ids)
        slot = {"evt": threading.Event(), "resp": None}
        with self._pending_lock:
            self._pending[rid] = slot
        try:
            data = (json.dumps({"id": rid, "method": method, "args": list(args)}) + "\n").encode()
            with self._send_lock:
                sock.sendall(data)
            if not slot["evt"].wait(self.timeout) or slot["resp"] is None:
                raise MotorNoDisponible(f"El motor no respondió a '{method}'")
        except OSError as exc:
            raise MotorNoDisponible(f"Error hablando con el motor: {exc}")
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)
        resp = slot["resp"]
        if not resp.get("ok"):
            if resp.get("type") == "ValueError":
                raise ValueError(resp.get("error"))
            raise RuntimeError(resp.get("error"))
        return resp.get("result")

    @property
    def status(self) -> dict:
        try:
            st = self._call("status")
            st["remoto"] = True
            return st
        except (MotorNoDisponible, RuntimeError) as exc:
            return {
                "backend": "remoto", "remoto": True, "running": False, "modo": None,
                "current_track": None, "position_sec": 0, "duration_sec": 0,
                "queue": [], "queue_length": 0, "pautas_pendientes": 0,
                "tandas_minutos": [], "proxima_tanda": None, "tanda_en_espera": 0,
                "salidas": {}, "preescucha": None,
                "crossfade_active": False, "crossfade_seg": 0,
                "should_be_playing": False, "is_playing": False,
                "last_error": str(exc),
            }

    def play_track(self, audio_id: int) -> bool:
        return bool(self._call("play_track", audio_id))

    def play_next(self):
        self._call("play_next")

    def queue_track(self, audio_id: int, nota: str = "", posicion=None) -> bool:
        return bool(self._call("queue_track", audio_id, nota, posicion))

    def cola_mover(self, uid: int, posicion: int):
        self._call("cola_mover", uid, posicion)

    def cola_quitar(self, uid: int):
        self._call("cola_quitar", uid)

    def plan_a_cola(self, uid: int, posicion: int = 0):
        self._call("plan_a_cola", uid, posicion)

    def clear_queue(self):
        self._call("clear_queue")

    def skip(self):
        self._call("skip")

    def stop_playback(self):
        self._call("stop_playback")

    def fire_effect(self, audio_id: int, canal_offset: int = 0, opciones=None) -> bool:
        return bool(self._call("fire_effect", audio_id, canal_offset, opciones or {}))

    def set_mode(self, mode: str):
        self._call("set_mode", mode)

    def lanzar_tanda(self, ahora: bool = False) -> int:
        return int(self._call("lanzar_tanda", ahora) or 0)

    def cue_play(self, audio_id: int, desde: float = 0.0) -> bool:
        return bool(self._call("cue_play", audio_id, desde))

    def cue_stop(self):
        self._call("cue_stop")

    def list_devices(self) -> list:
        return self._call("list_devices") or []

    def programa(self) -> dict:
        return self._call("programa") or {}

    def plan_quitar(self, uid: int):
        self._call("plan_quitar", uid)

    def plan_mover(self, uid: int, posicion: int):
        self._call("plan_mover", uid, posicion)

    def plan_regenerar(self):
        self._call("plan_regenerar")

    def plan_insertar(self, audio_id: int, posicion: int = 0) -> bool:
        return bool(self._call("plan_insertar", audio_id, posicion))

    def detener_al_terminar(self, activo: bool = True):
        self._call("detener_al_terminar", activo)

    def pausar(self, activo: bool = True):
        self._call("pausar", activo)

    def seek(self, segundos: float):
        self._call("seek", segundos)

    def tip_programar(self, audio_id: int, modo: str) -> bool:
        return bool(self._call("tip_programar", audio_id, modo))

    def tip_quitar(self):
        self._call("tip_quitar")

    def reload_config(self):
        try:
            self._call("reload_config")
        except MotorNoDisponible as exc:
            # Al reconectar no hace falta: el motor lee la config al arrancar.
            log.warning("[Motor remoto] reload_config no enviado: %s", exc)
