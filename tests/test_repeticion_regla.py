"""Regla «volver a sonar cada N tiempo»: por lote, hasta meses, y estado para el aviso en manual."""

from backend.database import get_connection
import pytest
from fastapi.testclient import TestClient

from test_relojes_api import subir


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def _repe(client, i):
    return client.get(f"/api/v1/biblioteca/{i}/ficha").json()["repeticion_horas"]


def test_lote_aplica_y_quita_la_regla(client, tmp_path):
    a = subir(client, tmp_path, "REP A")
    b = subir(client, tmp_path, "REP B")
    meses = 24 * 30 * 2                                    # «cada 2 meses» (30 días por mes)
    r = client.post("/api/v1/biblioteca/lote", json={"ids": [a, b], "cambios": {"repeticion_horas": meses}})
    assert r.status_code == 200, r.text
    assert _repe(client, a) == meses and _repe(client, b) == meses
    # 0 / null quitan la regla; fuera de rango se rechaza
    client.post("/api/v1/biblioteca/lote", json={"ids": [a], "cambios": {"repeticion_horas": None}})
    assert not _repe(client, a) and _repe(client, b) == meses
    assert client.post("/api/v1/biblioteca/lote", json={"ids": [a], "cambios": {"repeticion_horas": 24 * 365 * 9}}).status_code == 422


def test_individual_acepta_semanas_y_meses(client, tmp_path):
    a = subir(client, tmp_path, "REP C")
    assert client.put(f"/api/v1/audios/{a}", json={"repeticion_horas": 24 * 7 * 3}).status_code == 200
    assert _repe(client, a) == 504


def test_estado_para_el_aviso_en_manual(client, tmp_path):
    import sqlite3
    from backend.api.api_server import DB_PATH
    a = subir(client, tmp_path, "REP D")
    st = client.get(f"/api/v1/audios/{a}/repeticion").json()
    assert st["bloqueada"] is False                        # sin regla ni reproducción
    client.put(f"/api/v1/audios/{a}", json={"repeticion_horas": 5})
    conn = get_connection(DB_PATH)
    conn.execute("UPDATE audios SET ultima_reproduccion = datetime('now','localtime','-1 hours') WHERE id = ?", (a,))
    conn.commit()
    st = client.get(f"/api/v1/audios/{a}/repeticion").json()
    assert st["bloqueada"] is True and 230 <= st["faltan_min"] <= 241 and st["disponible"]
    conn.execute("UPDATE audios SET ultima_reproduccion = datetime('now','localtime','-6 hours') WHERE id = ?", (a,))
    conn.commit()
    assert client.get(f"/api/v1/audios/{a}/repeticion").json()["bloqueada"] is False
    conn.close()
    assert client.get("/api/v1/audios/999999/repeticion").status_code == 404
