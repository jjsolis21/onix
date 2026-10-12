"""Usuarios, contraseñas y roles para entrar desde otras PCs de la red."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def estudio():
    """La PC del estudio: entra sin contraseña (y arranca la app)."""
    from backend.api.api_server import app
    with TestClient(app) as c:
        for u, rol in (("jefe", "programacion"), ("cabina", "operador"), ("otroadmin", "admin")):
            r = c.post("/api/v1/usuarios", json={"usuario": u, "nombre": u.title(), "rol": rol,
                                                  "clave": "clave-segura-1"})
            assert r.status_code in (201, 409), r.text
        yield c


def remota(ip="192.168.1.50"):
    from backend.api.api_server import app
    return TestClient(app, client=(ip, 51000))


def entrar(c, usuario, clave="clave-segura-1"):
    return c.post("/api/v1/auth/login", json={"usuario": usuario, "clave": clave})


def test_la_pc_del_estudio_no_pide_contrasena(estudio):
    assert estudio.get("/api/v1/auth/yo").json()["rol"] == "admin"
    assert estudio.get("/").status_code == 200


def test_otra_pc_sin_sesion_va_al_login(estudio):
    c = remota()
    assert c.get("/api/v1/audios").status_code == 401
    r = c.get("/admin/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert c.get("/login").status_code == 200


def test_jefe_de_programacion_usa_el_admin_pero_no_el_aire(estudio):
    c = remota()
    r = entrar(c, "jefe")
    assert r.status_code == 200 and r.json()["inicio"] == "/admin/"
    assert c.get("/admin/").status_code == 200
    assert c.get("/api/v1/audios").status_code == 200
    assert c.post("/api/v1/relojes", json={"nombre": "Del jefe", "filas": []}).status_code == 201
    assert c.post("/api/v1/player/skip").status_code == 403                 # no opera el aire
    assert c.put("/api/v1/config/motor", json={"crossfade_seg": 3}).status_code == 403
    assert c.post("/api/v1/respaldos").status_code == 403
    assert c.get("/api/v1/usuarios").status_code == 403
    assert c.get("/", follow_redirects=False).headers["location"] == "/admin/"


def test_operador_opera_el_aire_pero_no_cambia_la_programacion(estudio):
    c = remota("192.168.1.60")
    assert entrar(c, "cabina").json()["inicio"] == "/"
    assert c.get("/").status_code == 200
    assert c.post("/api/v1/player/set_mode", json={"mode": "manual"}).status_code == 200
    assert c.post("/api/v1/relojes", json={"nombre": "X", "filas": []}).status_code == 403
    assert c.post("/api/v1/problemas/vistos").status_code == 200


def test_clave_mala_y_bloqueo_por_intentos(estudio):
    c = remota("192.168.1.70")
    for _ in range(5):
        assert entrar(c, "jefe", "mala").status_code == 401
    assert entrar(c, "jefe").status_code == 429                         # bloqueada 1 minuto


def test_cerrar_sesion_y_desactivar_usuario(estudio):
    c = remota("192.168.1.80")
    entrar(c, "jefe")
    assert c.get("/api/v1/audios").status_code == 200
    c.post("/api/v1/auth/logout")
    assert c.get("/api/v1/audios").status_code == 401
    entrar(c, "jefe")
    jefe = next(u for u in estudio.get("/api/v1/usuarios").json() if u["usuario"] == "jefe")
    estudio.put(f"/api/v1/usuarios/{jefe['id']}", json={**jefe, "activo": False})
    assert c.get("/api/v1/audios").status_code == 401                     # sesión cerrada
    estudio.put(f"/api/v1/usuarios/{jefe['id']}", json={**jefe, "activo": True})


def test_otra_web_no_puede_dar_ordenes(estudio):
    r = estudio.post("/api/v1/player/skip", headers={"Origin": "http://web-mala.com"})
    assert r.status_code == 403
    r = estudio.post("/api/v1/player/set_mode", json={"mode": "manual"},
                     headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_la_clave_no_se_guarda_en_claro(estudio):
    from backend.api.auth import hash_clave, verificar_clave
    h = hash_clave("secreto123")
    assert "secreto123" not in h and verificar_clave("secreto123", h) and not verificar_clave("otra", h)


def test_websocket_sin_sesion_no_conecta_y_programacion_no_opera(estudio):
    from starlette.websockets import WebSocketDisconnect
    c = remota("192.168.1.90")
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws") as ws:
            ws.receive_text()
    entrar(c, "jefe")
    with c.websocket_connect("/ws") as ws:
        ws.send_text('{"cmd": "skip"}')
        for _ in range(20):
            m = ws.receive_json()
            if m["event"] == "playback_error":
                assert m["data"]["reason"] == "sin_permiso"
                break
        else:
            pytest.fail("no llegó el rechazo")
