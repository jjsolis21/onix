"""Locutor IA: ciclo del servicio, aprobación y API (sin IA ni voz reales, sin internet)."""

import io
import sqlite3
import wave
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.ia import proveedores
from backend.locutor import almacen, noticias, servicio as svc

TZ = almacen.zona("America/Caracas")
AHORA = datetime(2026, 10, 13, 15, 0, 0, tzinfo=TZ)


def _wav(seg=3.0, hz=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(hz)
        w.writeframes(b"\x00\x01" * int(seg * hz))
    return buf.getvalue()


class MotorFalso:
    """Lo mínimo del motor que usa el Locutor: programa() y plan_insertar()."""

    def __init__(self, items):
        self.items = items
        self.insertados = []

    def programa(self):
        return {"items": [dict(i) for i in self.items]}

    def plan_insertar(self, audio_id, posicion=0):
        self.insertados.append((audio_id, posicion))
        self.items.insert(posicion, {"tipo": "audio", "audio_id": audio_id, "tipo_audio": "Locucion",
                                     "hora": "00:00:00", "titulo": "voz", "artista": "x"})
        return True


@pytest.fixture
def db(tmp_path):
    from scripts.init_db import initialize_database
    ruta = tmp_path / "loc.db"
    initialize_database(str(ruta))
    c = sqlite3.connect(ruta)
    c.row_factory = sqlite3.Row
    ids = []
    for i, (t, a) in enumerate([("Provenza", "Karol G"), ("Normal", "Feid"), ("Hawai", "Maluma"),
                                ("Despechá", "Rosalía"), ("Ojitos", "Bad Bunny")]):
        cur = c.execute("INSERT INTO audios (titulo, artista, duracion, archivo_path, tipo_audio)"
                        " VALUES (?, ?, 180, ?, 'Musica')", (t, a, f"library/{i}.mp3"))
        ids.append(cur.lastrowid)
    c.commit()
    c.close()
    return ruta, ids


def _config(ruta, **extra):
    c = almacen.conectar(ruta)
    almacen.guardar(c, "activo", True)
    almacen.guardar(c, "emisora", {"nombre": "Solís FM", "ciudad": "Ciudad Guayana", "zona_horaria": "America/Caracas"})
    almacen.guardar(c, "persona", {"nombre": "Britny", "voz": "fish:abc", "personalidad": "alegre"})
    almacen.guardar(c, "reglas", {"cada_canciones": 2, "tipos_permitidos": ["corta", "noticia", "identificacion"],
                                  **extra})
    c.commit()
    c.close()


NOMBRES = [("Provenza", "Karol G"), ("Normal", "Feid"), ("Hawai", "Maluma"), ("Despechá", "Rosalía"),
           ("Ojitos", "Bad Bunny")]


def _programa(ids, desde_min=5):
    out = []
    for n, aid in enumerate(ids):
        h = AHORA + timedelta(minutes=desde_min + 3 * n)
        t, a = NOMBRES[n]
        out.append({"tipo": "audio", "audio_id": aid, "tipo_audio": "Musica", "titulo": t, "artista": a,
                    "hora": h.strftime("%H:%M:%S")})
    return out


@pytest.fixture
def ia(monkeypatch):
    llamadas = {"texto": 0, "voz": 0}

    def texto(prompt, sistema="", db_path=None):
        llamadas["texto"] += 1
        return {"texto": "Seguimos en Solís FM.", "proveedor": "gemini"}

    def voz(t, v="", e="", db_path=None):
        llamadas["voz"] += 1
        llamadas["ultima_voz"] = (t, v)
        return {"wav": _wav(), "proveedor": "fish", "clave_id": 1, "intentos": []}
    monkeypatch.setattr(proveedores, "generar_texto", texto)
    monkeypatch.setattr(proveedores, "generar_voz", voz)
    return llamadas


def _estados(ruta):
    c = sqlite3.connect(ruta)
    try:
        return [r[0] for r in c.execute("SELECT estado FROM locutor_intervenciones ORDER BY id")]
    finally:
        c.close()


def test_huecos_cada_n_canciones():
    m = lambda: {"tipo": "audio", "tipo_audio": "Musica"}
    v = {"tipo": "audio", "tipo_audio": "Locucion"}
    assert svc.huecos([m(), m(), m(), m()], 2) == [1, 2]
    assert svc.huecos([m(), m(), v, m(), m()], 2) == []        # tras una voz hay que contar de nuevo
    assert svc.huecos([m(), m(), v, m(), m(), m()], 2) == [4]
    assert svc.huecos([m(), {"tipo": "tanda"}, m()], 1) == []     # entre una canción y una tanda no se habla
    assert svc.huecos([m()], 1) == []


def test_apagado_no_hace_nada(db, ia):
    ruta, ids = db
    c = almacen.conectar(ruta); almacen.guardar(c, "activo", False); c.commit(); c.close()
    motor = MotorFalso(_programa(ids))
    r = svc.ciclo(motor, ruta, AHORA)
    assert r["activo"] is False and ia["texto"] == 0 and motor.insertados == []


def test_corta_en_automatico_llega_al_programa_antes_de_sonar(db, ia):
    ruta, ids = db
    _config(ruta)
    motor = MotorFalso(_programa(ids))
    r = svc.ciclo(motor, ruta, AHORA)
    assert [h["estado"] for h in r["hecho"]] == ["aprobada"]
    assert ia["voz"] == 0                                    # recién escrita: la voz va en el ciclo siguiente
    r = svc.ciclo(motor, ruta, AHORA)                        # el segundo ciclo la locuta e inserta
    assert any(h.get("estado") == "lista" for h in r["hecho"])
    assert ia["voz"] == 1 and ia["ultima_voz"][1] == "fish:abc"
    assert len(motor.insertados) == 1
    audio_id, pos = motor.insertados[0]
    assert pos == 2                                          # justo tras la 2.ª canción
    c = sqlite3.connect(ruta)
    t = c.execute("SELECT tipo_audio, artista FROM audios WHERE id = ?", (audio_id,)).fetchone()
    assert t == ("Locucion", "Britny")
    # no repite el mismo hueco
    for _ in range(3):
        svc.ciclo(motor, ruta, AHORA)
    anteriores = [r[0] for r in sqlite3.connect(ruta).execute(
        "SELECT anterior_id FROM locutor_intervenciones")]
    assert len(anteriores) == len(set(anteriores))


def test_noticia_pide_aprobacion_y_no_gasta_voz_hasta_aprobar(db, ia, monkeypatch):
    ruta, ids = db
    _config(ruta, patron=["noticia"])
    c = almacen.conectar(ruta)
    c.execute("INSERT INTO locutor_fuentes (nombre, url, cierre) VALUES ('Primicia', 'https://x.test/feed', '')")
    c.execute("INSERT INTO locutor_noticias (fuente_id, guid, titulo, resumen, publicada_en) VALUES"
              " (1, 'g1', 'Inauguran nuevo puente', 'El puente une dos municipios', datetime('now'))")
    c.commit(); c.close()
    motor = MotorFalso(_programa(ids))
    # el texto de la IA debe respetar los datos permitidos
    monkeypatch.setattr(proveedores, "generar_texto", lambda prompt, sistema="", db_path=None: {
        "texto": "Inauguran nuevo puente, según Primicia.", "proveedor": "gemini"})
    svc.ciclo(motor, ruta, AHORA, leer_noticias=lambda u: [])
    svc.ciclo(motor, ruta, AHORA, leer_noticias=lambda u: [])
    assert _estados(ruta) == ["borrador", "aprobada"]     # la noticia espera; la corta siguiente va sola
    assert ia["voz"] == 0 and motor.insertados == []


def test_sin_ia_usa_plantilla_y_noticia_falla_sin_inventar(db, ia, monkeypatch):
    ruta, ids = db
    _config(ruta)

    def sin_ia(*a, **k):
        raise proveedores.IAError("sin claves")
    monkeypatch.setattr(proveedores, "generar_texto", sin_ia)
    c = almacen.conectar(ruta)
    r = svc.preparar(c, "corta", {"artista": "Karol G", "titulo": "Provenza"},
                     {"artista": "Feid", "titulo": "Normal"}, AHORA)
    assert r["estado"] == "aprobada" and "Feid" in r["texto"] and "Karol G" in r["texto"]
    r2 = svc.preparar(c, "noticia", None, None, AHORA)
    assert r2["estado"] == "fallo"
    c.close()


def test_si_la_ia_inventa_datos_se_cae_a_la_plantilla(db, ia, monkeypatch):
    ruta, ids = db
    _config(ruta)
    monkeypatch.setattr(proveedores, "generar_texto", lambda *a, **k: {
        "texto": "Suena Provenza, ganadora del Grammy en 2021, de Karol G. Sigue Feid.", "proveedor": "gemini"})
    c = almacen.conectar(ruta)
    r = svc.preparar(c, "corta", {"artista": "Karol G", "titulo": "Provenza"},
                     {"artista": "Feid", "titulo": "Normal"}, AHORA)
    assert "Grammy" not in r["texto"] and "Feid" in r["texto"]
    c.close()


def test_fuera_de_franja_no_habla(db, ia):
    ruta, ids = db
    _config(ruta, franjas=[["08:00", "12:00"]])
    motor = MotorFalso(_programa(ids))
    r = svc.ciclo(motor, ruta, AHORA)                        # son las 15:00
    assert ia["texto"] == 0 and r["hecho"][-1]["estado"] == "fuera_de_franja"


def test_rss_y_vetos():
    rss = b"""<?xml version="1.0"?><rss><channel><item><title>Abren la avenida</title>
      <description>&lt;p&gt;Texto &amp;amp; m\xc3\xa1s&lt;/p&gt;</description><link>https://x.test/1</link>
      <guid>g1</guid><pubDate>Mon, 12 Oct 2026 10:00:00 +0000</pubDate></item></channel></rss>"""
    e = noticias.parsear(rss)
    assert e[0]["titulo"] == "Abren la avenida" and e[0]["publicada_en"] == "2026-10-12 10:00:00"
    assert noticias.vetada({"titulo": "Nos unimos al dolor", "resumen": ""}, [])
    bomba = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel/></rss>'
    with pytest.raises(noticias.ErrorFuente):
        noticias.parsear(bomba)
    assert noticias.es_repetida("Inauguran nuevo puente sobre el Caroní", ["Nuevo puente sobre el Caroní inaugurado hoy"])


# ── API ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def test_api_activar_config_y_aprobacion(client):
    assert client.get("/api/v1/locutor/estado").json()["activo"] is False
    assert client.post("/api/v1/locutor/activar", json={"activo": True}).json() == {"activo": True}
    cfg = client.put("/api/v1/locutor/config", json={"aprobacion": {"noticia": "auto"},
                                                      "persona": {"nombre": "Britny", "voz": "fish:abc"}}).json()
    assert cfg["aprobacion"]["noticia"] == "auto" and cfg["persona"]["nombre"] == "Britny"
    assert client.put("/api/v1/locutor/config", json={"aprobacion": {"noticia": "quizás"}}).status_code == 422
    client.post("/api/v1/locutor/activar", json={"activo": False})


def test_api_mensaje_moderado_y_turnos(client):
    ok = client.post("/api/v1/locutor/mensajes", json={"de": "María", "texto": "Saludos a mi mamá"}).json()
    assert ok["estado"] == "pendiente"
    mal = client.post("/api/v1/locutor/mensajes", json={"de": "Pepe", "texto": "visita www.algo.com"}).json()
    assert mal["estado"] == "rechazado" and mal["motivo"]
    assert client.post(f"/api/v1/locutor/mensajes/{ok['id']}/aprobar").json()["estado"] == "aprobado"
    t = client.post("/api/v1/locutor/turnos", json={"desde": "08:00", "hasta": "12:00", "nombre": "Néstor",
                                                      "voz": "fish:def"}).json()
    assert any(x["nombre"] == "Néstor" for x in client.get("/api/v1/locutor/turnos").json())
    assert client.delete(f"/api/v1/locutor/turnos/{t['id']}").status_code == 200


def test_api_probar_y_aprobar_inserta_la_voz(client, ia, monkeypatch):
    import sqlite3 as sq
    from backend.api import locutor as api
    from backend.paths import DB_PATH
    c = sq.connect(DB_PATH)
    a = c.execute("INSERT INTO audios (titulo, artista, duracion, archivo_path, tipo_audio)"
                  " VALUES ('Provenza', 'Karol G', 180, 'library/p.mp3', 'Musica')").lastrowid
    b = c.execute("INSERT INTO audios (titulo, artista, duracion, archivo_path, tipo_audio)"
                  " VALUES ('Normal', 'Feid', 180, 'library/n.mp3', 'Musica')").lastrowid
    c.commit(); c.close()
    motor = MotorFalso([{"tipo": "audio", "audio_id": a, "tipo_audio": "Musica", "hora": "10:00:00"},
                        {"tipo": "audio", "audio_id": b, "tipo_audio": "Musica", "hora": "10:03:00"}])
    monkeypatch.setattr(api, "engine", motor)
    client.put("/api/v1/locutor/config", json={"persona": {"nombre": "Britny", "voz": "fish:abc"}})
    r = client.post("/api/v1/locutor/intervenciones/probar",
                    json={"tipo": "corta", "anterior_id": a, "siguiente_id": b})
    assert r.status_code == 201 and r.json()["estado"] == "borrador"
    assert ia["voz"] == 0                                  # nada de voz hasta aprobar
    iid = r.json()["id"]
    ap = client.post(f"/api/v1/locutor/intervenciones/{iid}/aprobar", json={"texto": "Sigue Feid con Normal."})
    assert ap.status_code == 200, ap.text
    assert ap.json()["estado"] == "lista" and ap.json()["en_programa"] is True
    assert motor.insertados and motor.insertados[0][1] == 1
    lista = client.get("/api/v1/locutor/intervenciones?estado=lista").json()
    assert any(x["id"] == iid and x["texto"] == "Sigue Feid con Normal." for x in lista)
    assert client.post(f"/api/v1/locutor/intervenciones/{iid}/descartar").status_code == 200


def test_api_importar_hace_copia_previa(client, tmp_path):
    o = tmp_path / "locutor.db"
    x = sqlite3.connect(o)
    x.executescript("""
    CREATE TABLE clientes (id INTEGER PRIMARY KEY, slug TEXT, nombre TEXT, ciudad TEXT, genero TEXT,
        zona_horaria TEXT, modo TEXT, entrega_cifrada TEXT);
    CREATE TABLE personas_dj (cliente_id INTEGER, nombre TEXT, personalidad TEXT, muletillas TEXT, prohibidos TEXT,
        proveedor_voz TEXT, voz_id TEXT, pronunciacion TEXT);
    CREATE TABLE reglas (cliente_id INTEGER, intervenciones_por_hora INTEGER, tipos_permitidos TEXT, franjas TEXT);
    CREATE TABLE turnos (id INTEGER PRIMARY KEY, cliente_id INTEGER, desde TEXT, hasta TEXT, nombre TEXT,
        voz_id TEXT, personalidad TEXT);
    CREATE TABLE patrocinios (id INTEGER PRIMARY KEY, cliente_id INTEGER, texto TEXT, desde TEXT, hasta TEXT,
        frecuencia INTEGER, activo INTEGER);
    CREATE TABLE fuentes_noticias (id INTEGER PRIMARY KEY, cliente_id INTEGER, nombre TEXT, url TEXT, activo INTEGER);
    INSERT INTO clientes VALUES (1, 'solisfm', 'Solís FM', 'Ciudad Guayana', '', 'America/Caracas', 'A', 'X');
    INSERT INTO personas_dj VALUES (1, 'Britny', 'alegre', '[]', '[]', 'fish', 'abc123', '{}');
    INSERT INTO reglas VALUES (1, 4, '[]', '[]');
    INSERT INTO turnos VALUES (1, 1, '08:00', '12:00', 'Néstor', 'def', '');
    """)
    x.commit(); x.close()
    for t in client.get("/api/v1/locutor/turnos").json():
        client.delete(f"/api/v1/locutor/turnos/{t['id']}")
    r = client.post("/api/v1/locutor/importar", json={"origen": str(o), "reemplazar": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["importado"] is True and d["turnos"] == 1 and d["copia_previa"].endswith(".db")
    from pathlib import Path
    assert Path(d["copia_previa"]).is_file()
    assert client.post("/api/v1/locutor/importar", json={"origen": str(tmp_path / "no.db")}).status_code == 404


def test_api_voces_para_elegir_y_editar_turno(client):
    c = almacen.conectar(__import__("backend.paths", fromlist=["DB_PATH"]).DB_PATH)
    almacen.guardar(c, "voces", [{"nombre": "Karen", "voz": "fish:629d", "proveedor": "fish", "genero": "femenina"}])
    c.commit(); c.close()
    v = client.get("/api/v1/locutor/voces").json()["voces"]
    karen = next(x for x in v if x["nombre"] == "Karen")
    assert karen["voz"] == "fish:629d" and karen["disponible"] is False      # no hay clave de Fish en las pruebas
    assert any(x["proveedor"] == "gemini" for x in v)
    t = client.post("/api/v1/locutor/turnos", json={"desde": "08:00", "hasta": "12:00", "nombre": "Néstor", "voz": "fish:def"}).json()
    r = client.put(f"/api/v1/locutor/turnos/{t['id']}", json={"desde": "09:00", "hasta": "13:00", "nombre": "Karen", "voz": "fish:629d"})
    assert r.status_code == 200
    fila = next(x for x in client.get("/api/v1/locutor/turnos").json() if x["id"] == t["id"])
    assert (fila["desde"], fila["nombre"], fila["voz"]) == ("09:00", "Karen", "fish:629d")
    assert client.put("/api/v1/locutor/turnos/99999", json={"desde": "09:00", "hasta": "13:00", "nombre": "X", "voz": "y"}).status_code == 404
    client.delete(f"/api/v1/locutor/turnos/{t['id']}")


def test_si_el_tipo_del_patron_falla_el_hueco_no_se_pierde(db, ia, monkeypatch):
    """Sin IA y sin plantilla para «noticia»: entra una corta en ese mismo hueco y el patrón sigue avanzando."""
    ruta, ids = db
    _config(ruta, patron=["noticia", "corta"])
    c = almacen.conectar(ruta)
    c.execute("INSERT INTO locutor_fuentes (nombre, url) VALUES ('P', 'https://x.test/f')")
    c.execute("INSERT INTO locutor_noticias (fuente_id, guid, titulo, publicada_en) VALUES (1, 'g', 'Abren el puente', datetime('now'))")
    c.commit(); c.close()

    def sin_ia(*a, **k):
        raise proveedores.IAError("HTTP 404: modelo no disponible")
    monkeypatch.setattr(proveedores, "generar_texto", sin_ia)
    motor = MotorFalso(_programa(ids))
    r = svc.ciclo(motor, ruta, AHORA, leer_noticias=lambda u: [])
    assert [h["estado"] for h in r["hecho"]] == ["fallo", "aprobada"]
    assert r["hecho"][1]["tipo"] == "corta" and "Feid" in r["hecho"][1]["texto"]
    # el siguiente hueco sigue el patrón (el intento fallido contó): ya no insiste con la misma noticia
    r2 = svc.ciclo(motor, ruta, AHORA, leer_noticias=lambda u: [])
    assert any(h.get("tipo") == "corta" for h in r2["hecho"])
