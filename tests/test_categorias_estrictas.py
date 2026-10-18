"""Géneros y categorías solo de la lista (sin inventar) y música que llega completa suena sola."""

import pytest
from fastapi.testclient import TestClient

from backend.database import get_connection
from conftest import make_wav


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def _subir(client, tmp_path, titulo, **datos):
    wav = make_wav(tmp_path / f"{titulo}.wav", seconds=2.0)
    with open(wav, "rb") as fh:
        r = client.post("/api/v1/audios", data={"titulo": titulo, "artista": f"ART {titulo}", **datos},
                        files={"file": (wav.name, fh, "audio/wav")})
    assert r.status_code == 201, r.text
    return r.json()


def _fila(aid):
    c = get_connection()
    f = c.execute("SELECT subgenero, categoria, aprobada FROM audios WHERE id = ?", (aid,)).fetchone()
    c.close()
    return f["subgenero"], f["categoria"], bool(f["aprobada"])


def _insertar(titulo, subgenero=None, categoria=None, aprobada=0):
    c = get_connection()
    aid = c.execute("INSERT INTO audios (titulo, artista, duracion, archivo_path, tipo_audio, subgenero, categoria, aprobada)"
                    " VALUES (?, 'X', 60, ?, 'Musica', ?, ?, ?)", (titulo, f"library/{titulo}.mp3", subgenero, categoria, aprobada)).lastrowid
    c.commit()
    c.close()
    return aid


# ── La lista no admite repetidos ──────────────────────────────────────────────────────────────

def test_no_se_puede_crear_un_valor_repetido_solo_por_mayusculas_o_tildes(client):
    r = client.post("/api/v1/config/biblioteca/valores", json={"nombre_interno": "cat1", "valor": "POP"})
    assert r.status_code == 409 and "Pop" in r.json()["detail"]
    assert client.post("/api/v1/config/biblioteca/valores", json={"nombre_interno": "voz", "valor": "DUO"}).status_code == 409
    assert client.post("/api/v1/config/biblioteca/valores", json={"nombre_interno": "cat1", "valor": "Vallenato"}).status_code == 201
    assert client.post("/api/v1/config/biblioteca/valores", json={"nombre_interno": "cat1", "valor": "vallenato"}).status_code == 409


def test_las_voces_oficiales_estan_en_la_lista(client):
    voces = {v["valor"] for cat in client.get("/api/v1/config/biblioteca/schema").json()["categorias"]
             if cat["nombre_interno"] == "voz" for v in cat["valores"]}
    assert {"Hombre", "Mujer", "Dúo", "Grupo", "FT", "Genérico"} <= voces
    assert "MALE" not in voces and "Instrumental" not in voces                # MALE es «Hombre»; Instrumental ya no se usa


# ── El sistema no inventa géneros ─────────────────────────────────────────────────────────────

def test_editar_con_un_genero_que_no_existe_se_rechaza(client):
    aid = _insertar("EDIT GENERO")
    r = client.put(f"/api/v1/audios/{aid}", json={"cat1": "Genero Inventado"})
    assert r.status_code == 422 and "no está en la lista" in r.json()["detail"]
    assert _fila(aid)[0] is None
    r = client.put(f"/api/v1/audios/{aid}", json={"cat1": "pop"})            # existe: se guarda con la escritura oficial
    assert r.status_code == 200 and _fila(aid)[0] == "Pop"


def test_una_cancion_antigua_con_valor_fuera_de_la_lista_sigue_pudiendo_editar_su_titulo(client):
    aid = _insertar("CANCION VIEJA", subgenero="Valor Antiguo", categoria="Nuevo", aprobada=1)
    assert client.put(f"/api/v1/audios/{aid}", json={"titulo": "Cambiado"}).status_code == 200
    assert _fila(aid)[0] == "Valor Antiguo"                                  # no se toca lo que no se pidió


def test_el_lote_tampoco_admite_generos_inventados(client):
    aid = _insertar("LOTE GENERO")
    r = client.post("/api/v1/biblioteca/lote", json={"ids": [aid], "cambios": {"cat1": "Inventado"}})
    assert r.status_code == 422


def test_subida_con_genero_de_las_etiquetas_que_no_esta_en_la_lista(client, tmp_path):
    r = _subir(client, tmp_path, "SUBE RARO", cat1="Genero Raro", cat2="Nuevo")
    assert r["data"]["cat1"] is None and r["data"]["cat2"] == "Nuevo"
    assert r["lista_para_sonar"] is False and r["faltan"] == ["género"]
    assert any("Genero Raro" in a for a in r["avisos"])
    assert _fila(r["data"]["id"]) == (None, "Nuevo", False)                  # queda «Por completar»


# ── Música completa suena sola; la incompleta se completa y se aprueba sola ───────────────────

def test_subida_completa_queda_lista_con_la_escritura_oficial(client, tmp_path):
    r = _subir(client, tmp_path, "SUBE COMPLETA", cat1="pop", cat2="nuevo")
    assert r["lista_para_sonar"] is True and r["faltan"] == [] and r["avisos"] == []
    assert _fila(r["data"]["id"]) == ("Pop", "Nuevo", True)


def test_subida_sin_nada_dice_que_falta(client, tmp_path):
    r = _subir(client, tmp_path, "SUBE VACIA")
    assert r["lista_para_sonar"] is False and r["faltan"] == ["género", "rotación"]


def test_al_completar_lo_que_faltaba_se_aprueba_sola(client):
    aid = _insertar("PENDIENTE UNO", subgenero="Pop")                        # falta la rotación
    r = client.put(f"/api/v1/audios/{aid}", json={"cat2": "Nuevo"})
    assert r.status_code == 200 and r.json()["data"]["aprobada_sola"] is True
    assert _fila(aid)[2] is True


def test_una_cancion_completa_devuelta_a_la_bandeja_a_proposito_no_se_aprueba_sola(client):
    aid = _insertar("RETENIDA", subgenero="Pop", categoria="Nuevo", aprobada=0)
    r = client.put(f"/api/v1/audios/{aid}", json={"cat2": "Reciente"})
    assert r.status_code == 200 and r.json()["data"]["aprobada_sola"] is False
    assert _fila(aid)[2] is False


def test_el_lote_completa_y_aprueba_solo_las_que_faltaban(client):
    falta = _insertar("LOTE FALTA", subgenero="Rock")
    vacia = _insertar("LOTE VACIA")
    retenida = _insertar("LOTE RETENIDA", subgenero="Rock", categoria="Nuevo", aprobada=0)
    r = client.post("/api/v1/biblioteca/lote", json={"ids": [falta, vacia, retenida], "cambios": {"cat1": "Rock", "cat2": "Reciente"}})
    assert r.status_code == 200 and r.json()["aprobadas_solas"] == 2
    assert _fila(falta)[2] is True and _fila(vacia)[2] is True
    assert _fila(retenida)[2] is False                                       # la que alguien retuvo no se toca


# ── La migración limpia lo que ya estaba repetido ─────────────────────────────────────────────

def test_la_migracion_unifica_los_repetidos_y_reasigna_las_canciones():
    from backend.migraciones_maria import migrar
    c = get_connection()
    # Se reproduce la base de antes: columna que distingue mayúsculas, con «POP» y «DUO» además de «Pop» y «Dúo»
    c.execute("ALTER TABLE config_categorias_valores MODIFY `valor` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL")
    cat1 = c.execute("SELECT id FROM config_categorias_def WHERE nombre_interno = 'cat1'").fetchone()[0]
    voz = c.execute("SELECT id FROM config_categorias_def WHERE nombre_interno = 'voz'").fetchone()[0]
    c.execute("INSERT INTO config_categorias_valores (cat_id, valor, orden) VALUES (?, 'POP', 90)", (cat1,))
    c.execute("INSERT INTO config_categorias_valores (cat_id, valor, orden) VALUES (?, 'DUO', 91)", (voz,))
    c.execute("INSERT INTO config_categorias_valores (cat_id, valor, orden) VALUES (?, 'TEMP_TEST', 92)", (cat1,))
    c.commit()
    a = c.execute("INSERT INTO audios (titulo, artista, duracion, archivo_path, tipo_audio, subgenero, genero_vocal)"
                  " VALUES ('M1', 'X', 60, 'library/m1.mp3', 'Musica', 'POP', 'DUO')").lastrowid
    c.commit()
    c.close()

    migrar()

    c = get_connection()
    assert tuple(c.execute("SELECT subgenero, genero_vocal FROM audios WHERE id = ?", (a,)).fetchone()) == ("Pop", "Dúo")
    nombres = [r[0] for r in c.execute("SELECT valor FROM config_categorias_valores WHERE cat_id = ?", (cat1,)).fetchall()]
    assert "Pop" in nombres and "POP" not in nombres and "TEMP_TEST" not in nombres
    assert c.execute("SELECT collation_name FROM information_schema.columns WHERE table_schema = DATABASE()"
                     " AND table_name = 'config_categorias_valores' AND column_name = 'valor'").fetchone()[0] == "utf8mb4_unicode_ci"
    c.close()
