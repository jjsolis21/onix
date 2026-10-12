"""API de relojes, grilla y programa."""

import time

import pytest
from fastapi.testclient import TestClient

from conftest import make_wav


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def subir(client, tmp_path, titulo, tipo="Musica", cat2=None, seconds=3.0):
    wav = make_wav(tmp_path / f"{titulo}.wav", seconds=seconds)
    data = {"titulo": titulo, "artista": f"ART {titulo}", "tipo_audio": tipo}
    if cat2:
        data["cat2"] = cat2
    with open(wav, "rb") as fh:
        r = client.post("/api/v1/audios", data=data, files={"file": (wav.name, fh, "audio/wav")})
    assert r.status_code == 201, r.text
    aid = r.json()["data"]["id"]
    # Lo subido entra a la Bandeja (no suena solo): en estas pruebas se aprueba
    # directo; si no, el resultado dependía de otras pruebas que aprobaban.
    import sqlite3
    from backend.paths import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE audios SET aprobada = 1 WHERE id = ?", (aid,))
    conn.commit()
    conn.close()
    return aid


def test_crud_de_reloj(client, tmp_path):
    jid = subir(client, tmp_path, "ID RELOJ", tipo="Efecto")
    subir(client, tmp_path, "EXITO RELOJ", cat2="EXITO")
    r = client.post("/api/v1/relojes", json={
        "nombre": "Mañana", "color": "#ee9b00",
        "filas": [{"tipo_audio": "efecto", "audio_id": jid},
                  {"tipo_audio": "Musica", "filtros": {"cat2": "exito", "basura": "x"}}],
    })
    assert r.status_code == 201, r.text
    reloj = r.json()
    assert reloj["filas"][0]["tipo_audio"] == "Efecto"
    assert reloj["filas"][1]["filtros"] == {"cat2": "EXITO"}          # normaliza y limpia

    assert client.post("/api/v1/relojes", json={"nombre": "Mañana"}).status_code == 409
    assert client.post("/api/v1/relojes", json={"nombre": "X", "filas": [{"tipo_audio": "Cuña"}]}).status_code == 422

    vista = client.get(f"/api/v1/relojes/{reloj['id']}/vista").json()
    assert [f["candidatos"] for f in vista["filas"]] == [1, 1]

    copia = client.post(f"/api/v1/relojes/{reloj['id']}/duplicar").json()
    assert copia["nombre"] == "Mañana (copia)" and len(copia["filas"]) == 2

    r = client.put(f"/api/v1/relojes/{copia['id']}", json={"nombre": "Tarde", "filas": []})
    assert r.status_code == 200 and r.json()["filas"] == []
    assert client.delete(f"/api/v1/relojes/{copia['id']}").status_code == 204


def test_grilla(client):
    rid = client.get("/api/v1/relojes").json()[0]["id"]
    r = client.put("/api/v1/relojes/grilla", json={
        "reloj_id": rid, "celdas": [{"dia": d, "hora": h} for d in range(7) for h in range(6, 10)]})
    assert r.status_code == 200 and len(r.json()) == 28
    r = client.put("/api/v1/relojes/grilla", json={"reloj_id": None, "celdas": [{"dia": 0, "hora": 6}]})
    assert len(r.json()) == 27
    assert client.put("/api/v1/relojes/grilla", json={"reloj_id": rid, "celdas": [{"dia": 7, "hora": 0}]}).status_code == 422


def test_programa(client, tmp_path):
    for i in range(5):
        subir(client, tmp_path, f"PROG {i}", seconds=60)
    client.post("/api/v1/programa/regenerar")
    for _ in range(40):
        prog = client.get("/api/v1/programa").json()
        if prog["items"]:
            break
        time.sleep(0.05)
    assert prog["items"] and prog["items"][0]["hora"]
    uid = prog["items"][0]["uid"]
    client.post(f"/api/v1/programa/{uid}/quitar")
    time.sleep(0.2)
    assert uid not in [i["uid"] for i in client.get("/api/v1/programa").json()["items"]]


def test_config_planificador_y_salida_cartuchera(client):
    r = client.put("/api/v1/config/motor", json={"horas_planificadas": 3, "aleatoriedad": 0})
    assert r.json()["horas_planificadas"] == 3 and r.json()["aleatoriedad"] == 0
    r = client.get("/api/v1/audio/dispositivos").json()
    assert r["salidas"]["cartuchera"] == -2
    assert client.put("/api/v1/audio/salidas", json={"cartuchera": 2}).json()["salidas"]["cartuchera"] == 2
    client.put("/api/v1/audio/salidas", json={"cartuchera": -2})
    client.put("/api/v1/config/motor", json={"horas_planificadas": 2, "aleatoriedad": 3})


def test_eventos_a_hora_fija_crud(client):
    rid = client.post("/api/v1/relojes", json={"nombre": "Para evento", "filas": [{"tipo_audio": "Musica"}]}).json()["id"]
    r = client.post("/api/v1/relojes/eventos", json={"nombre": "Noticias", "reloj_id": rid, "hora": "13:05",
                                                     "dias": [1, 2, 3, 4, 5], "inmediato": True})
    assert r.status_code == 201, r.text
    ev = r.json()
    assert ev["hora"] == "13:05" and ev["dias"] == [1, 2, 3, 4, 5] and ev["reloj"] == "Para evento"
    assert client.post("/api/v1/relojes/eventos", json={"nombre": "X", "reloj_id": rid, "hora": "25:00"}).status_code == 422
    assert client.post("/api/v1/relojes/eventos", json={"nombre": "X", "reloj_id": rid, "hora": "10:00",
                                                        "dias": []}).status_code == 422
    r = client.put(f"/api/v1/relojes/eventos/{ev['id']}", json={**ev, "hora": "13:10", "fecha_fin": "2026-12-31"})
    assert r.json()["hora"] == "13:10"
    assert any(e["id"] == ev["id"] for e in client.get("/api/v1/relojes/eventos").json())
    assert client.delete(f"/api/v1/relojes/eventos/{ev['id']}").status_code == 204
    assert client.delete(f"/api/v1/relojes/eventos/{ev['id']}").status_code == 404


def test_vista_previa_y_dia_especifico(client, tmp_path):
    subir(client, tmp_path, "PREV A", cat2="PREVIA", seconds=3.0)
    r = client.post("/api/v1/relojes/vista-previa", json={"filas": [
        {"tipo_audio": "Musica", "filtros": {"cat2": "PREVIA"}},
        {"tipo_audio": "Musica", "filtros": {"cat2": "NO EXISTE"}}]})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["filas"][0]["candidatos"] >= 1 and v["filas"][1]["candidatos"] == 0
    assert v["saltadas"] == [1] and v["filas"][1]["inicio"] >= 0
    rid = client.post("/api/v1/relojes", json={"nombre": "Feriado", "filas": []}).json()["id"]
    r = client.put("/api/v1/relojes/dias", json={"fecha": "2030-12-24", "horas": [20, 21], "reloj_id": rid})
    assert r.status_code == 200 and len(r.json()) == 2
    client.put("/api/v1/relojes/dias", json={"fecha": "2030-12-24", "horas": [21], "accion": "musica"})
    client.put("/api/v1/relojes/dias", json={"fecha": "2030-12-24", "horas": [20], "accion": "semana"})
    horas = client.get("/api/v1/relojes/dias", params={"fecha": "2030-12-24"}).json()
    assert horas == [{"fecha": "2030-12-24", "hora": 21, "reloj_id": None}]
    assert any(d["fecha"] == "2030-12-24" for d in client.get("/api/v1/relojes/dias").json())


def test_reloj_guarda_respaldo_y_lo_cuenta_en_la_vista(client, tmp_path):
    subir(client, tmp_path, "RESP A", cat2="RESPALDO1")
    r = client.post("/api/v1/relojes", json={"nombre": "Con resp", "respaldo": {"cat2": "respaldo1"}, "filas": []})
    assert r.status_code == 201 and r.json()["respaldo"] == {"cat2": "RESPALDO1"}
    dup = client.post(f"/api/v1/relojes/{r.json()['id']}/duplicar").json()
    assert dup["respaldo"] == {"cat2": "RESPALDO1"}
    v = client.post("/api/v1/relojes/vista-previa", json={"filas": [], "respaldo": {"cat2": "RESPALDO1"}}).json()
    assert v["respaldo_candidatos"] >= 1


def test_evento_guarda_dura_hasta(client):
    rid = client.post("/api/v1/relojes", json={"nombre": "Ev dura", "filas": []}).json()["id"]
    r = client.post("/api/v1/relojes/eventos", json={"nombre": "Especial", "reloj_id": rid, "hora": "20:30",
                                                      "hasta": "21:15", "fecha_inicio": "2030-01-05", "fecha_fin": "2030-01-05"})
    assert r.status_code == 201 and r.json()["hasta"] == "21:15"
    assert client.post("/api/v1/relojes/eventos", json={"nombre": "Mal", "reloj_id": rid, "hora": "20:30",
                                                        "hasta": "20:30"}).status_code == 422


def test_playlist_desde_reloj_y_lanzar(client, tmp_path):
    for i in range(3):
        subir(client, tmp_path, f"PL {i}", cat2="PLAYL", seconds=60)
    rid = client.post("/api/v1/relojes", json={"nombre": "Para playlist",
                                               "filas": [{"tipo_audio": "Musica", "filtros": {"cat2": "PLAYL"}}]}).json()["id"]
    r = client.post("/api/v1/playlists/generar", json={"nombre": "Noche larga", "reloj_id": rid, "horas": 0.25})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["audios"] >= 15 and p["reloj"] == "Para playlist"        # 15 min con temas de 1 min (se repiten)
    assert any(x["id"] == p["id"] for x in client.get("/api/v1/playlists", params={"q": "noche"}).json())
    assert client.post("/api/v1/playlists/generar", json={"nombre": "Noche larga", "reloj_id": rid, "horas": 1}).status_code == 409
    r = client.post(f"/api/v1/playlists/{p['id']}/lanzar", json={"reemplazar": True})
    assert r.status_code == 200 and r.json()["encoladas"] == p["audios"]
    client.post("/api/v1/player/clear_queue")
    assert client.delete(f"/api/v1/playlists/{p['id']}").status_code == 204


def test_playlist_manual_programada_y_segun_grilla(client, tmp_path):
    a = subir(client, tmp_path, "MAN A", cat2="MANUAL", seconds=30)
    b = subir(client, tmp_path, "MAN B", cat2="MANUAL", seconds=30)
    r = client.post("/api/v1/playlists", json={"nombre": "Armada", "items": [a, b, a]})
    assert r.status_code == 201, r.text
    p = r.json()
    assert [i["audio_id"] for i in p["items"]] == [a, b, a]
    r = client.put(f"/api/v1/playlists/{p['id']}", json={"nombre": "Armada", "items": [b, a],
                                                          "programada": "2030-02-01T20:30", "inmediato": True})
    assert r.json()["programada"] == "2030-02-01T20:30" and r.json()["inmediato"] == 1
    assert [i["audio_id"] for i in r.json()["items"]] == [b, a]
    r = client.post("/api/v1/playlists/generar", json={"nombre": "Grilla", "reloj_id": None, "horas": 0.25,
                                                       "inicio": "2030-02-01T10:00"})
    assert r.status_code == 201, r.text and r.json()["audios"] > 0
    assert client.post("/api/v1/playlists/generar", json={"nombre": "Sin hora", "reloj_id": None, "horas": 1,
                                                          "programar": True}).status_code == 422


def test_operador_puede_lanzar_playlists():
    from backend.api.auth import permiso
    assert permiso("operador", "POST", "/api/v1/playlists/3/lanzar")
    assert not permiso("operador", "POST", "/api/v1/playlists")
    assert permiso("programacion", "POST", "/api/v1/playlists/generar")


def test_importar_m3u_y_pls_busca_en_la_biblioteca(client, tmp_path):
    a = subir(client, tmp_path, "Camino Al Sol", seconds=10)
    b = subir(client, tmp_path, "Noche Larga", seconds=10)
    m3u = ("#EXTM3U\n"
           f"#EXTINF:10,ART Camino Al Sol - Camino Al Sol\nC:\\Musica\\Camino Al Sol.wav\n"
           "#EXTINF:200,Zzqx Nadie - Qwvz Inexistente\nD:/otra/zzqx_qwvz_inexistente.mp3\n"
           "#EXTINF:10,ART NOCHE LARGA - Noche Larga\n\\\\SERVIDOR\\temas\\nóche_larga_remix.mp3\n")
    r = client.post("/api/v1/playlists/importar", data={"nombre": "Importada M3U"},
                    files={"archivo": ("lista.m3u", m3u.encode("cp1252"), "audio/x-mpegurl")})
    assert r.status_code == 201, r.text
    d = r.json()
    assert [i["audio_id"] for i in d["items"]] == [a, b] and d["encontradas"] == 2
    assert d["no_encontradas"] == ["Zzqx Nadie - Qwvz Inexistente"] and d["total_lista"] == 3
    pls = "[playlist]\nFile1=/x/Noche Larga.mp3\nTitle1=Noche Larga\nNumberOfEntries=1\n"
    r = client.post("/api/v1/playlists/importar", files={"archivo": ("otra.pls", pls.encode(), "text/plain")})
    assert r.status_code == 201 and r.json()["nombre"] == "otra" and r.json()["items"][0]["audio_id"] == b
    r = client.post("/api/v1/playlists/importar", files={"archivo": ("vacia.m3u", b"#EXTM3U\n/x/zzz.mp3\n", "text/plain")})
    assert r.status_code == 422


def test_memos_viajan_con_la_cancion_hasta_el_studio(client, tmp_path):
    a = subir(client, tmp_path, "Con Memo", seconds=30)
    r = client.post("/api/v1/playlists", json={"nombre": "Con memos",
                                                "items": [{"audio_id": a, "nota": "Saludar a Maracay"}, a]})
    assert r.status_code == 201, r.text
    p = r.json()
    assert [i["nota"] for i in p["items"]] == ["Saludar a Maracay", ""]
    client.post("/api/v1/player/clear_queue")
    assert client.post(f"/api/v1/playlists/{p['id']}/lanzar", json={"reemplazar": True}).json()["encoladas"] == 2
    cola = client.get("/api/v1/programa").json()["cola"]
    assert cola[0]["nota"] == "Saludar a Maracay"
    client.post("/api/v1/player/clear_queue")
