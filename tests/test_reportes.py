"""Estadísticas, historial con filtros, reporte de pautas y salidas de audio."""

import json
import os
import sqlite3
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.api.reportes import _cumplimiento_pauta
from conftest import make_wav


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def db():
    conn = sqlite3.connect(os.environ["RADIO_DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def nuevo_audio(titulo, tipo="Musica", ultima=None):
    conn = db()
    cur = conn.execute(
        "INSERT INTO audios (titulo, artista, archivo_path, tipo_audio, ultima_reproduccion)"
        " VALUES (?, 'ART', ?, ?, ?)", (titulo, f"x/{titulo}.mp3", tipo, ultima))
    conn.commit()
    conn.close()
    return cur.lastrowid


def emitir(audio_id, cuando, tipo="musica", pauta_id=None, titulo="T"):
    conn = db()
    conn.execute(
        "INSERT INTO historial_emision (audio_id, titulo, artista, tipo, origen, pauta_id, slot,"
        " iniciado_en) VALUES (?, ?, 'ART', ?, ?, ?, ?, ?)",
        (audio_id, titulo, tipo, "pauta" if pauta_id else "auto", pauta_id,
         cuando.strftime("%Y-%m-%d-%H") if pauta_id else None,
         cuando.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


def test_top40_y_detalle(client):
    hit = nuevo_audio("EXITO TOP")
    otro = nuevo_audio("MENOS TOP")
    ahora = datetime.now()
    for i in range(5):
        emitir(hit, ahora - timedelta(hours=i), titulo="EXITO TOP")
    emitir(otro, ahora, titulo="MENOS TOP")
    emitir(otro, ahora - timedelta(days=20), titulo="MENOS TOP")   # fuera de la semana

    top = client.get("/api/v1/stats/top40", params={"period": "week"}).json()
    ids = [t["id"] for t in top]
    assert ids.index(hit) < ids.index(otro)
    fila = next(t for t in top if t["id"] == otro)
    assert fila["toques"] == 1 and fila["tipo"] == "TEMA"
    assert client.get("/api/v1/stats/top40", params={"period": "año"}).status_code == 422

    det = client.get(f"/api/v1/stats/detalle/{hit}").json()
    assert det["total_toques"] == 5 and len(det["emisiones"]) == 5
    assert client.get("/api/v1/stats/detalle/999999").status_code == 404


def test_olvidados(client):
    viejo = nuevo_audio("VIEJO", ultima=(datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"))
    reciente = nuevo_audio("RECIENTE", ultima=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ids = {o["id"]: o for o in client.get("/api/v1/stats/top40/forgotten").json()}
    assert viejo in ids and ids[viejo]["dias_sin_sonar"] >= 89
    assert reciente not in ids


def test_historial_filtros_y_csv(client):
    aid = nuevo_audio("FILTRABLE")
    emitir(aid, datetime.now(), titulo="FILTRABLE")
    r = client.get("/api/v1/historial/buscar", params={"q": "FILTRABLE"}).json()
    assert r["total"] == 1 and r["data"][0]["titulo"] == "FILTRABLE"
    csv = client.get("/api/v1/historial/buscar", params={"q": "FILTRABLE", "formato": "csv"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert "FILTRABLE" in csv.text
    assert client.get("/api/v1/historial/buscar", params={"desde": "ayer"}).status_code == 422


def test_cumplimiento_pauta():
    """Lunes 12-oct-2026, pauta en 3 horas del lunes; salió en 2."""
    conn = db()
    aid = nuevo_audio("CUÑA CLIENTE", tipo="Cuña")
    matriz = {"1-8": True, "1-9": True, "1-10": True}
    cur = conn.execute(
        "INSERT INTO pautas (cliente, audio_id, matriz, fecha_inicio, fecha_fin)"
        " VALUES ('CLIENTE X', ?, ?, '2026-10-12', '2026-10-12')", (aid, json.dumps(matriz)))
    conn.commit()
    pid = cur.lastrowid
    emitir(aid, datetime(2026, 10, 12, 8, 15), tipo="pauta", pauta_id=pid)
    emitir(aid, datetime(2026, 10, 12, 9, 15), tipo="pauta", pauta_id=pid)
    p = conn.execute("SELECT * FROM pautas WHERE id = ?", (pid,)).fetchone()

    dia = date(2026, 10, 12)
    r = _cumplimiento_pauta(conn, p, dia, dia, ahora=datetime(2026, 10, 13, 0, 0))
    assert (r["programadas"], r["emitidas"], r["faltantes"]) == (3, 2, 1)
    assert r["cumplimiento_pct"] == pytest.approx(66.7)

    # A las 9:30 del mismo día solo cuenta la hora 8 (ya terminada) y la 9 (ya emitida)
    r = _cumplimiento_pauta(conn, p, dia, dia, ahora=datetime(2026, 10, 12, 9, 30))
    assert (r["programadas"], r["emitidas"]) == (2, 2)
    conn.close()


def test_reporte_pautas_endpoint(client):
    r = client.get("/api/v1/reportes/pautas",
                   params={"desde": "2026-10-01", "hasta": "2026-10-31", "cliente": "cliente x"})
    assert r.status_code == 200
    fila = r.json()["data"][0]
    assert fila["cliente"] == "CLIENTE X" and fila["emitidas"] == 2
    det = client.get(f"/api/v1/reportes/pautas/{fila['pauta_id']}",
                     params={"desde": "2026-10-01", "hasta": "2026-10-31"}).json()
    assert len(det["emisiones"]) == 2 and det["dias"][0]["fecha"] == "2026-10-12"
    csv = client.get("/api/v1/reportes/pautas", params={"formato": "csv", "desde": "2026-10-01",
                                                        "hasta": "2026-10-31"})
    assert "CLIENTE X" in csv.text


# ── Salidas de audio y comandos nuevos ───────────────────────────────────────

def test_dispositivos_y_salidas(client):
    r = client.get("/api/v1/audio/dispositivos").json()
    assert {d["index"] for d in r["dispositivos"]} == {0, 1, 2}     # mock
    assert r["salidas"] == {"aire": -1, "preescucha": -1, "cartuchera": -2}

    r = client.put("/api/v1/audio/salidas", json={"preescucha": 2})
    assert r.status_code == 200 and r.json()["salidas"]["preescucha"] == 2
    assert client.get("/api/v1/status").json()["salidas"]["preescucha"] == 2
    assert client.put("/api/v1/audio/salidas", json={"aire": 7}).status_code == 422
    client.put("/api/v1/audio/salidas", json={"preescucha": -1})


def test_preescucha_por_api(client, tmp_path):
    wav = make_wav(tmp_path / "pre.wav", seconds=5)
    with open(wav, "rb") as fh:
        aid = client.post("/api/v1/audios", data={"titulo": "Pre", "artista": "A"},
                          files={"file": ("pre.wav", fh, "audio/wav")}).json()["data"]["id"]
    assert client.post("/api/v1/player/cue", json={"audio_id": aid}).status_code == 200
    import time
    for _ in range(40):
        st = client.get("/api/v1/status").json()
        if st["preescucha"]:
            break
        time.sleep(0.05)
    assert st["preescucha"]["audio_id"] == aid
    assert st["current_track"] is None or st["current_track"]["id"] != aid   # no sale al aire
    client.post("/api/v1/player/cue_stop")


def test_lanzar_tanda_sin_tanda(client):
    r = client.post("/api/v1/player/lanzar_tanda", json={"ahora": True})
    assert r.status_code == 422 and "tanda" in r.json()["detail"]


def test_pauta_sin_fecha_inicio_cuenta_desde_su_creacion():
    """Una pauta creada hoy no debe aparecer como incumplida en semanas anteriores."""
    conn = db()
    aid = nuevo_audio("CUÑA NUEVA", tipo="Cuña")
    cur = conn.execute(
        "INSERT INTO pautas (cliente, audio_id, matriz, created_at)"
        " VALUES ('NUEVO', ?, ?, '2026-10-12 08:00:00')", (aid, json.dumps({"1-8": True})))
    conn.commit()
    p = conn.execute("SELECT * FROM pautas WHERE id = ?", (cur.lastrowid,)).fetchone()
    # Los lunes 5 y 12 tienen la hora 8 marcada; solo cuenta el 12
    r = _cumplimiento_pauta(conn, p, date(2026, 10, 1), date(2026, 10, 12),
                            ahora=datetime(2026, 10, 13, 0, 0))
    assert r["programadas"] == 1
    conn.close()


def test_certificado_imprimible_por_cliente(client, tmp_path):
    from tests.test_api import subir_cancion
    aid = subir_cancion(client, tmp_path, "Cuña Cert")["id"]
    r = client.post("/api/v1/pautas", json={"cliente": "panaderia sol", "audio_id": aid,
                                             "audio_nombre": "Cuña Cert", "matriz": {"1-10": True},
                                             "menciones_diarias": 2})
    assert r.status_code == 201, r.text
    r = client.get("/api/v1/reportes/certificado", params={"cliente": "PANADERIA SOL"})
    assert r.status_code == 200
    assert "Certificado de emisión" in r.text and "PANADERIA SOL" in r.text
    assert client.get("/api/v1/reportes/certificado", params={"cliente": "NADIE"}).status_code == 404


def test_pautas_en_riesgo_cuando_no_alcanzan_las_horas(tmp_db):
    import json, sqlite3
    from datetime import datetime
    from backend.api.reportes import pautas_en_riesgo
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    aid = conn.execute("INSERT INTO audios (titulo, artista, archivo_path) VALUES ('c','a','c.wav')").lastrowid
    # Lunes 12/10/2026: marcada a las 8, 12 y 18, 3 por día
    m = json.dumps({"1-8": True, "1-12": True, "1-18": True})
    pid = conn.execute("INSERT INTO pautas (cliente, audio_id, matriz, menciones_diarias) VALUES ('X', ?, ?, 3)",
                       (aid, m)).lastrowid
    conn.commit()
    # 13:00 sin ninguna salida: faltan 3 y solo queda la de las 18 → en riesgo
    r = pautas_en_riesgo(conn, datetime(2026, 10, 12, 13, 0))
    assert r and r[0]["pauta_id"] == pid and r[0]["perdidas"] == 2 and r[0]["estado"] == "en_riesgo"
    # 7:00: quedan las 3 horas → bien
    assert pautas_en_riesgo(conn, datetime(2026, 10, 12, 7, 0)) == []
    # 19:00 con 1 salida: incumplida
    conn.execute("INSERT INTO historial_emision (titulo, pauta_id, slot) VALUES ('c', ?, '2026-10-12-08')", (pid,))
    conn.commit()
    r = pautas_en_riesgo(conn, datetime(2026, 10, 12, 19, 0))
    assert r[0]["estado"] == "incumplida" and r[0]["emitidas"] == 1


def test_analisis_por_hora_y_artistas(client):
    import sqlite3
    from datetime import datetime
    from backend.paths import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    hora = datetime.now().replace(minute=1, second=0).strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(3):
        conn.execute("INSERT INTO historial_emision (titulo, artista, tipo, iniciado_en) VALUES ('t', 'SOLIS', 'musica', ?)", (hora,))
    conn.commit()
    conn.close()
    d = client.get("/api/v1/stats/analisis", params={"period": "day"}).json()
    assert d["horas"][datetime.now().hour] >= 3
    assert any(a["artista"] == "SOLIS" and a["toques"] >= 3 for a in d["artistas"])
    assert len(d["pautas_mes"]) == 6


def test_top40_por_tipo_y_tendencia(client):
    for tipo in ("musica", "pauta", "todo"):
        r = client.get("/api/v1/stats/top40", params={"period": "week", "tipo": tipo})
        assert r.status_code == 200, r.text
        assert all("previos" in x for x in r.json())
    assert client.get("/api/v1/stats/top40", params={"tipo": "x"}).status_code == 422


def test_rotacion_y_quemadas(client):
    import sqlite3
    from datetime import datetime
    from backend.paths import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    ids = [conn.execute("INSERT INTO audios (titulo, artista, archivo_path, tipo_audio, categoria, aprobada)"
                        " VALUES (?, 'X', ?, 'Musica', 'ROTQ', 1)", (f"q{i}", f"q{i}.mp3")).lastrowid for i in range(5)]
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(12):
        conn.execute("INSERT INTO historial_emision (audio_id, titulo, tipo, iniciado_en) VALUES (?, 'q0', 'musica', ?)", (ids[0], ahora))
    conn.execute("INSERT INTO historial_emision (audio_id, titulo, tipo, iniciado_en) VALUES (?, 'q1', 'musica', ?)", (ids[1], ahora))
    conn.commit()
    conn.close()
    rot = client.get("/api/v1/stats/rotacion", params={"period": "week"}).json()
    f = next(x for x in rot["data"] if x["rotacion"] == "ROTQ")
    assert (f["canciones"], f["toques"], f["sin_sonar"]) == (5, 13, 3)
    q = client.get("/api/v1/stats/quemadas").json()["data"]
    assert any(x["id"] == ids[0] for x in q) and not any(x["id"] == ids[1] for x in q)
