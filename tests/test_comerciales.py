"""Comerciales: catálogo de clientes y agencias, y chequeo de integridad."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.paths import DB_PATH


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def _db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _audio(c, titulo, archivo="library/cuna.mp3", activo=1, dur=20, tipo="Cuña"):
    return c.execute("INSERT INTO audios (titulo, artista, duracion, archivo_path, tipo_audio, activo)"
                     " VALUES (?, 'PUBLICIDAD', ?, ?, ?, ?)", (titulo, dur, archivo, tipo, activo)).lastrowid


def _pauta(c, cliente, audio_id, matriz=None, inicio="", fin="", rubro="Bebidas", agencia=""):
    return c.execute(
        "INSERT INTO pautas (cliente, audio_id, audio_nombre, matriz, fecha_inicio, fecha_fin, rubro, agencia, activo)"
        " VALUES (?, ?, 'x', ?, ?, ?, ?, ?, 1)",
        (cliente, audio_id, json.dumps(matriz if matriz is not None else {"1-10": True}), inicio, fin, rubro, agencia)).lastrowid


def test_catalogo_crear_editar_renombrar_y_quitar(client):
    c = _db()
    a = _audio(c, "Cuña A")
    _pauta(c, "PEPSI", a, agencia="MEDIOS SUR")
    c.commit(); c.close()

    r = client.post("/api/v1/comerciales/catalogo/clientes", json={"nombre": " pepsi  ", "telefono": "0424"})
    assert r.status_code == 201 and r.json()["nombre"] == "PEPSI"
    assert client.post("/api/v1/comerciales/catalogo/clientes", json={"nombre": "Pepsi"}).status_code == 409
    assert client.get("/api/v1/comerciales/catalogo/otro").status_code == 404

    lista = client.get("/api/v1/comerciales/catalogo/clientes").json()
    pepsi = next(x for x in lista["items"] if x["nombre"] == "PEPSI")
    assert pepsi["spots"] == 1 and pepsi["telefono"] == "0424"

    # importar trae las agencias que ya usan los spots; repetir no duplica
    assert client.post("/api/v1/comerciales/catalogo/agencias/importar").json()["agregados"] >= 1
    assert client.post("/api/v1/comerciales/catalogo/agencias/importar").json()["agregados"] == 0
    assert "MEDIOS SUR" in [x["nombre"] for x in client.get("/api/v1/comerciales/catalogo/agencias").json()["items"]]

    # renombrar actualiza los spots que lo usaban
    r = client.put(f"/api/v1/comerciales/catalogo/clientes/{pepsi['id']}", json={"nombre": "PepsiCo", "telefono": "0424"})
    assert r.status_code == 200 and r.json()["spots_actualizados"] == 1
    c = _db()
    assert c.execute("SELECT COUNT(*) FROM pautas WHERE cliente = 'PEPSICO'").fetchone()[0] == 1
    c.close()
    assert client.delete(f"/api/v1/comerciales/catalogo/clientes/{pepsi['id']}").status_code == 200
    c = _db()
    assert c.execute("SELECT COUNT(*) FROM pautas WHERE cliente = 'PEPSICO'").fetchone()[0] == 1   # el spot conserva el texto
    c.close()


def test_el_chequeo_encuentra_lo_que_no_va_a_sonar(client, tmp_path):
    from backend.paths import MEDIA_ROOT
    bueno = MEDIA_ROOT / "library" / "chequeo_ok.mp3"
    bueno.parent.mkdir(parents=True, exist_ok=True)
    bueno.write_bytes(b"x")
    c = _db()
    ok = _audio(c, "Cuña OK", "library/chequeo_ok.mp3")
    perdido = _audio(c, "Cuña perdida", "library/no_existe_jamas.mp3")
    (MEDIA_ROOT / "library" / "chequeo_papelera.mp3").write_bytes(b"x")
    papelera = _audio(c, "Cuña en papelera", "library/chequeo_papelera.mp3", activo=0)
    _pauta(c, "CLIENTE BIEN", ok, rubro="Bancos")
    _pauta(c, "CLIENTE SIN HORARIO", ok, matriz={}, rubro="Bancos")
    _pauta(c, "CLIENTE ARCHIVO", perdido, rubro="Bancos")
    _pauta(c, "CLIENTE PAPELERA", papelera, rubro="Bancos")
    _pauta(c, "CLIENTE VENCIDO", ok, fin="2020-01-01", rubro="Bancos")
    _pauta(c, "CLIENTE FECHAS", ok, inicio="2026-06-10", fin="2026-06-01", rubro="Bancos")
    _pauta(c, "CLIENTE SIN RUBRO", ok, rubro="")
    c.commit(); c.close()

    r = client.get("/api/v1/comerciales/chequeo").json()
    por = {}
    for p in r["problemas"]:
        por.setdefault(p["cliente"], set()).add((p["nivel"], p["codigo"]))
    assert "CLIENTE BIEN" not in por
    assert ("error", "sin_horarios") in por["CLIENTE SIN HORARIO"]
    assert ("error", "archivo_perdido") in por["CLIENTE ARCHIVO"]
    assert ("error", "audio_inactivo") in por["CLIENTE PAPELERA"]
    assert ("aviso", "vencido") in por["CLIENTE VENCIDO"]
    assert ("error", "fechas") in por["CLIENTE FECHAS"]
    assert ("aviso", "sin_rubro") in por["CLIENTE SIN RUBRO"]
    assert r["errores"] >= 4 and r["avisos"] >= 2 and r["revisados"] >= 7
    # los errores van primero
    niveles = [p["nivel"] for p in r["problemas"]]
    assert niveles == sorted(niveles, key=lambda n: n != "error")


def test_grilla_de_tandas_api(client):
    r = client.put("/api/v1/comerciales/tandas-grilla",
                   json={"grilla": {"1-10": {"minutos": "45, 15", "inmediatas": "15"}, "0-3": {"off": True}}})
    assert r.status_code == 200
    g = client.get("/api/v1/comerciales/tandas-grilla").json()
    assert g["grilla"] == {"1-10": {"minutos": "15,45", "inmediatas": "15"}, "0-3": {"off": True}}
    assert "tandas_minutos" in g["general"]
    assert client.put("/api/v1/comerciales/tandas-grilla", json={"grilla": {"7-10": {"minutos": "0"}}}).status_code == 422
    assert client.put("/api/v1/comerciales/tandas-grilla", json={"grilla": {"1-10": {"minutos": "70"}}}).status_code == 422
    assert client.put("/api/v1/comerciales/tandas-grilla", json={"grilla": {"1-10": {"minutos": ""}}}).status_code == 422


def test_tandas_del_dia_muestra_cada_bloque(client):
    import json
    c = _db()
    a = _audio(c, "Cuña día", "library/dia.mp3")
    _pauta(c, "CLIENTE DIA", a, rubro="Bancos", matriz={"1-10": True})
    c.execute("INSERT INTO config (clave, valor) VALUES ('tandas_minutos', '15,45')"
              " ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor")
    c.execute("INSERT INTO config (clave, valor) VALUES ('tandas_grilla', ?)"
              " ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (json.dumps({"1-11": {"off": True}}),))
    c.commit(); c.close()
    r = client.get("/api/v1/comerciales/tandas-dia", params={"fecha": "2026-10-12"}).json()   # lunes
    assert len(r["horas"]) == 24
    h10 = r["horas"][10]
    assert [b["minuto"] for b in h10["bloques"]] == [15, 45]
    assert any(x["cliente"] == "CLIENTE DIA" for b in h10["bloques"] for x in b["cunas"])
    assert r["horas"][11]["sin_tandas"] is True and r["horas"][11]["bloques"] == []
    assert client.get("/api/v1/comerciales/tandas-dia", params={"fecha": "mal"}).status_code == 422
