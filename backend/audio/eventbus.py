"""
eventbus.py — Ónix FM
Bus de eventos pub/sub. Lo usan el motor (audio_engine.py), el servicio del
motor (engine_service.py) y su cliente en la API (engine_client.py).
"""

import logging
import threading
from typing import Callable

log = logging.getLogger("AudioEngine")

WILDCARD = "*"   # suscribirse a "*" recibe todos los eventos


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
            if event != WILDCARD:
                callbacks += self._listeners.get(WILDCARD, [])
        for cb in callbacks:
            try:
                cb(event, payload)
            except Exception as exc:
                log.error("[EventBus] Error en '%s': %s", event, exc)
