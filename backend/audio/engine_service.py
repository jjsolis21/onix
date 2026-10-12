"""
engine_service.py — Ónix FM
Motor de audio como PROCESO APARTE del servidor web.

Así reiniciar o actualizar la API (o que se caiga) no corta el aire: el
motor sigue sonando y la API se reconecta sola al volver.

Arranque (desde la raíz del proyecto):
    python -m backend.audio.engine_service
y la API con ONIX_ENGINE=remoto (ver scripts/iniciar_onix.ps1).

PROTOCOLO (TCP en 127.0.0.1, una línea JSON por mensaje):
    API → motor   {"id": 7, "method": "play_track", "args": [42]}
    motor → API   {"id": 7, "ok": true, "result": true}
                  {"id": 7, "ok": false, "error": "...", "type": "ValueError"}
    motor → API   {"event": "track_started", "data": {...}}   (sin pedirlo)

Solo escucha en la máquina local (127.0.0.1). Variables:
    ONIX_ENGINE_HOST (127.0.0.1)   ONIX_ENGINE_PORT (8766)
"""

import json
import logging
import os
import queue
import signal
import socket
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(_ROOT), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.audio.eventbus import WILDCARD  # noqa: E402

log = logging.getLogger("EngineService")

HOST = os.getenv("ONIX_ENGINE_HOST", "127.0.0.1")
PORT = int(os.getenv("ONIX_ENGINE_PORT", "8766"))

# Lo único que la API puede pedirle al motor
METHODS = {
    "status", "play_track", "play_next", "queue_track", "clear_queue", "skip",
    "stop_playback", "fire_effect", "set_mode", "reload_config",
    "lanzar_tanda", "cue_play", "cue_stop", "list_devices",
    "programa", "plan_quitar", "plan_mover", "plan_regenerar",
    "plan_insertar", "detener_al_terminar", "seek", "pausar",
}


def _dump(msg: dict) -> bytes:
    return (json.dumps(msg, default=str, ensure_ascii=False) + "\n").encode("utf-8")


class _Client:
    """
    Una conexión de la API. Lo que se le envía pasa por una cola con su
    propio hilo escritor: si la API se cuelga, el motor NUNCA se bloquea
    esperando al socket (se descartan eventos y se cierra la conexión).
    """

    MAX_PENDIENTES = 2000

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.cola: "queue.Queue[bytes | None]" = queue.Queue(self.MAX_PENDIENTES)
        threading.Thread(target=self._writer, name="EngineClientTx", daemon=True).start()

    def send(self, msg: dict) -> bool:
        try:
            self.cola.put_nowait(_dump(msg))
            return True
        except queue.Full:
            log.error("[Servicio] La API no lee sus mensajes: se cierra la conexión")
            self.close()
            return False

    def _writer(self):
        while True:
            data = self.cola.get()
            if data is None:
                break
            try:
                self.sock.sendall(data)
            except OSError:
                break
        self.close()

    def close(self):
        # shutdown primero: con un makefile() abierto, close() solo no corta
        # la conexión y la API no se enteraría de que el motor se fue.
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            self.cola.put_nowait(None)
        except queue.Full:
            pass


class EngineServer:
    def __init__(self, engine, host: str = HOST, port: int = PORT):
        self.engine = engine
        self.host, self.port = host, port
        self._clients: list[_Client] = []
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._running = False

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def start(self):
        """Abre el puerto y atiende conexiones en un hilo. Devuelve el puerto real."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":   # en Windows SO_REUSEADDR permite robar el puerto
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._running = True
        self.engine.bus.subscribe(WILDCARD, self._on_event)
        threading.Thread(target=self._accept_loop, name="EngineServer", daemon=True).start()
        log.info("[Servicio] Motor escuchando en %s:%d", self.host, self.port)
        return self.port

    def stop(self):
        with self._lock:
            self._running = False
            clientes, self._clients = self._clients, []
        self.engine.bus.unsubscribe(WILDCARD, self._on_event)
        if self._sock:
            # shutdown despierta al hilo bloqueado en accept(); sin esto el
            # puerto queda ocupado y un motor nuevo no puede volver a abrirlo.
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
        for c in clientes:
            c.close()

    # ── Conexiones ───────────────────────────────────────────────────────────

    def _accept_loop(self):
        while self._running:
            try:
                sock, addr = self._sock.accept()
            except OSError:
                break
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client = _Client(sock)
            with self._lock:
                if not self._running:      # se aceptó justo mientras se detenía
                    client.close()
                    break
                self._clients.append(client)
            log.info("[Servicio] API conectada desde %s:%d", *addr)
            threading.Thread(target=self._client_loop, args=(client,),
                             name="EngineClientRx", daemon=True).start()

    def _client_loop(self, client: _Client):
        try:
            with client.sock.makefile("r", encoding="utf-8") as reader:
                for line in reader:
                    line = line.strip()
                    if line:
                        client.send(self._handle(line))
        except (OSError, ValueError):
            pass
        finally:
            with self._lock:
                if client in self._clients:
                    self._clients.remove(client)
            client.close()
            log.info("[Servicio] API desconectada (el motor sigue sonando)")

    def _handle(self, line: str) -> dict:
        try:
            req = json.loads(line)
            rid, method, args = req.get("id"), req.get("method"), req.get("args") or []
        except (ValueError, AttributeError):
            return {"id": None, "ok": False, "error": "JSON inválido", "type": "ValueError"}
        if method not in METHODS:
            return {"id": rid, "ok": False, "error": f"Método no permitido: {method}",
                    "type": "ValueError"}
        try:
            attr = getattr(self.engine, method)
            result = attr if method == "status" else attr(*args)
            return {"id": rid, "ok": True, "result": result}
        except Exception as exc:
            return {"id": rid, "ok": False, "error": str(exc), "type": type(exc).__name__}

    def _on_event(self, event: str, data: dict):
        with self._lock:
            clientes = list(self._clients)
        for c in clientes:
            c.send({"event": event, "data": data})


def main():
    from init_db import initialize_database
    from backend.audio.audio_engine import engine
    from backend.paths import DB_PATH

    initialize_database(str(DB_PATH))     # el motor puede arrancar antes que la API
    engine.start()
    server = EngineServer(engine)
    try:
        server.start()
    except OSError as exc:
        log.critical("[Servicio] No se pudo abrir %s:%d (%s). ¿Ya hay otro motor corriendo?",
                     HOST, PORT, exc)
        engine.stop()
        sys.exit(1)

    parar = threading.Event()
    signal.signal(signal.SIGINT, lambda *a: parar.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *a: parar.set())
    if hasattr(signal, "SIGBREAK"):              # Ctrl+Break / cierre de consola en Windows
        signal.signal(signal.SIGBREAK, lambda *a: parar.set())
    while not parar.wait(1.0):
        pass
    log.info("[Servicio] Deteniendo motor...")
    server.stop()
    engine.stop()


if __name__ == "__main__":
    main()
