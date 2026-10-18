"""Control del aire: una sola PC opera a la vez, y el rol «consulta» solo mira."""

import json

import pytest
from fastapi.testclient import TestClient

from backend.api import auth as auth_api


def test_consulta_solo_lee():
    assert auth_api.permiso("consulta", "GET", "/api/v1/audios")
    assert auth_api.permiso("consulta", "GET", "/api/v1/programa/")
    assert not auth_api.permiso("consulta", "POST", "/api/v1/audios")
    assert not auth_api.permiso("consulta", "PUT", "/api/v1/pautas/1")
    assert not auth_api.permiso("consulta", "DELETE", "/api/v1/cartuchera/1")
    assert not auth_api.permiso("consulta", "GET", "/api/v1/usuarios")       # datos de cuentas: solo admin
    assert not auth_api.puede_operar_aire("consulta")


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def _recibir(ws, tipo):
    """Lee mensajes hasta el tipo pedido (ignora los demás: el motor emite eventos sin parar)."""
    for _ in range(200):
        m = json.loads(ws.receive_text())
        if m.get("event") == tipo:
            return m
    raise AssertionError(f"no llegó {tipo}")


def test_una_pc_tiene_el_aire_y_la_otra_recibe_aviso(client):
    from backend.api import api_server
    api_server._control.update(ws=None, usuario="")
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        a.send_text(json.dumps({"cmd": "stop"}))           # la primera que opera se queda con el control
        b.send_text(json.dumps({"cmd": "stop"}))           # la segunda no puede
        aviso = _recibir(b, "playback_error")
        assert aviso["data"]["reason"] == "sin_control"
        assert api_server._control["ws"] is not None


def test_tomar_el_control_y_la_otra_pc_queda_avisada(client):
    from backend.api import api_server
    api_server._control.update(ws=None, usuario="")
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        a.send_text(json.dumps({"cmd": "stop"}))
        b.send_text(json.dumps({"cmd": "tomar_control"}))
        perdido = _recibir(a, "playback_error")
        assert perdido["data"]["reason"] == "control_perdido"
        b.send_text(json.dumps({"cmd": "stop"}))           # ahora sí, la que tomó el control opera
        assert api_server._control["ws"] is not None


def test_si_la_conexion_se_corta_de_golpe_el_control_tambien_queda_libre(client):
    """Una PC que se cae (cable, apagado) no debe dejar el aire bloqueado para las demás."""
    import time
    from backend.api import api_server
    api_server._control.update(ws=None, usuario="")
    with pytest.raises(RuntimeError):
        with client.websocket_connect("/ws") as a:
            a.send_text(json.dumps({"cmd": "stop"}))
            assert api_server._control["ws"] is not None
            raise RuntimeError("se cayó la red")            # sale sin cierre limpio del websocket
    for _ in range(60):
        if api_server._control["ws"] is None:
            break
        time.sleep(0.05)
    assert api_server._control["ws"] is None


def test_al_cerrar_la_pc_el_control_queda_libre(client):
    from backend.api import api_server
    api_server._control.update(ws=None, usuario="")
    with client.websocket_connect("/ws") as a:
        a.send_text(json.dumps({"cmd": "stop"}))
        assert api_server._control["ws"] is not None
    import time
    for _ in range(60):                                     # el servidor lo suelta en su propio hilo, un instante después
        if api_server._control["ws"] is None:
            break
        time.sleep(0.05)
    assert api_server._control["ws"] is None                # se soltó al cerrar
