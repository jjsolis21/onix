"""
Motor como proceso aparte: servidor (engine_service) y cliente (engine_client)
hablando por un socket real en 127.0.0.1. El motor corre en modo mock.
"""

import sqlite3
import threading
import time

import pytest

from backend.audio.audio_engine import AudioEngine
from backend.audio.engine_client import RemoteEngine
from backend.audio.engine_service import EngineServer
from conftest import make_wav


def esperar(cond, segundos=3.0):
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def motor(tmp_db, tmp_path):
    eng = AudioEngine(db_path=tmp_db, respaldo_dir=tmp_path / "respaldo")
    eng.start()
    yield eng
    eng.stop()


@pytest.fixture
def servidor(motor):
    srv = EngineServer(motor, port=0)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def cliente(servidor):
    cli = RemoteEngine(port=servidor.port, timeout=2.0)
    cli.RECONNECT_SECS = 0.1
    cli.start()
    assert cli.wait_connected(3)
    yield cli
    cli.stop()


def add_audio(db, path, titulo="TEMA"):
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO audios (titulo, artista, archivo_path) VALUES (?, 'ARTISTA', ?)",
        (titulo, str(path)),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def test_status_y_comandos(cliente, motor, tmp_db, tmp_path):
    st = cliente.status
    assert st["running"] is True and st["remoto"] is True

    eventos = []
    cliente.bus.subscribe("track_started", lambda e, d: eventos.append(d))
    aid = add_audio(tmp_db, make_wav(tmp_path / "a.wav", seconds=3))
    assert cliente.play_track(aid) is True
    assert esperar(lambda: eventos)
    assert eventos[0]["audio_id"] == aid
    assert cliente.status["current_track"]["id"] == aid

    assert cliente.play_track(999999) is False
    with pytest.raises(ValueError):
        cliente.set_mode("cualquiera")
    cliente.set_mode("automatico")
    assert motor._mode == "automatico"


def test_api_caida_no_detiene_el_motor(cliente, motor, tmp_db, tmp_path):
    aid = add_audio(tmp_db, make_wav(tmp_path / "b.wav", seconds=5))
    cliente.play_track(aid)
    assert esperar(lambda: motor.status["current_track"] is not None)
    cliente.stop()                                   # "se reinicia la API"
    time.sleep(0.2)
    assert motor.status["current_track"]["id"] == aid


def test_reconexion(servidor, motor):
    cli = RemoteEngine(port=servidor.port, timeout=1.0)
    cli.RECONNECT_SECS = 0.1
    caidas = []
    cli.bus.subscribe("engine_stopped", lambda e, d: caidas.append(d))
    cli.start()
    try:
        assert cli.wait_connected(3)
        port = servidor.port
        servidor.stop()                              # se cae el motor
        assert esperar(lambda: caidas)
        assert cli.status["running"] is False
        with pytest.raises(ConnectionError):
            cli.skip()

        nuevo = EngineServer(motor, port=port)       # vuelve el motor
        nuevo.start()
        try:
            assert esperar(lambda: cli.connected, 5)
            assert cli.status["running"] is True
        finally:
            nuevo.stop()
    finally:
        cli.stop()


def test_metodo_no_permitido(servidor):
    import json
    import socket
    with socket.create_connection(("127.0.0.1", servidor.port)) as s:
        s.sendall(b'{"id": 1, "method": "stop", "args": []}\n')
        resp = json.loads(s.makefile().readline())
    assert resp["ok"] is False and "no permitido" in resp["error"]


def test_cliente_sin_motor():
    cli = RemoteEngine(port=1, timeout=0.5)   # nada escucha ahí
    cli.start()
    try:
        time.sleep(0.1)
        st = cli.status
        assert st["running"] is False and st["last_error"]
    finally:
        cli.stop()
