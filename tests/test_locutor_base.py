"""Locutor IA, base: guion, validación, moderación, almacén e importación (sin salir a internet)."""

import json
import sqlite3

import pytest

from backend.ia import proveedores
from backend.locutor import almacen, guion, moderacion, plantillas
from backend.locutor.metadatos import limpiar_pista
from backend.locutor.validador import validar_guion


@pytest.fixture
def c(tmp_path):
    con = almacen.conectar(tmp_path / "t.db")
    yield con
    con.close()


def _ctx(**kw):
    base = dict(emisora="Solís FM", ciudad="Ciudad Guayana", momento="tarde", hora_local="15:45",
                anterior={"artista": "Karol G", "titulo": "Provenza"}, siguiente={"artista": "Feid", "titulo": "Normal"})
    base.update(kw)
    return guion.Contexto(**base)


def test_prompt_lleva_las_reglas_y_solo_los_datos_dados():
    sis, usr = guion.construir_prompt({"nombre": "Britny", "personalidad": "alegre"}, _ctx(), "corta", 8)
    assert "CERO DATOS INVENTADOS" in sis and "Britny" in sis and "Solís FM" in sis
    assert "Karol G - Provenza" in usr and "Feid - Normal" in usr


def test_generar_guion_usa_las_claves_de_admin_ia(monkeypatch):
    visto = {}

    def falso(prompt, sistema="", db_path=None):
        visto["prompt"], visto["sistema"] = prompt, sistema
        return {"texto": ' "Sigue Feid con Normal." ', "proveedor": "gemini"}
    monkeypatch.setattr(proveedores, "generar_texto", falso)
    g = guion.generar_guion({"nombre": "Britny"}, _ctx(), "corta")
    assert g.texto == "Sigue Feid con Normal." and g.modelo == "gemini"
    assert "Feid - Normal" in visto["prompt"]


def test_sin_ia_lanza_errorguion(monkeypatch):
    def falla(*a, **k):
        raise proveedores.IAError("sin claves")
    monkeypatch.setattr(proveedores, "generar_texto", falla)
    with pytest.raises(guion.ErrorGuion):
        guion.generar_guion({"nombre": "X"}, _ctx(), "corta")
    with pytest.raises(guion.ErrorGuion):
        guion.generar_guion({"nombre": "X"}, _ctx(), "inventado")


def test_validador_frena_datos_inventados():
    permitido = "Solís FM Karol G Provenza Feid Normal Britny"
    assert validar_guion("Suena Provenza de Karol G.", permitido) == []
    assert validar_guion("Suena Provenza, ganadora del Grammy en 2021.", permitido)


def test_plantilla_de_emergencia_no_inventa():
    p = {"nombre": "Britny"}
    t = plantillas.guion_de_emergencia("corta", p, _ctx())
    assert "Karol G" in t and "Feid" in t
    assert plantillas.guion_de_emergencia("noticia", p, _ctx()) is None
    assert plantillas.hora_hablada(15, 45, "tarde") == "Son las tres y cuarenta y cinco de la tarde"


def test_moderacion_de_mensajes():
    assert moderacion.revisar(["Un saludo para mi mamá"]) is None
    assert moderacion.revisar(["escríbeme a www.algo.com"]) is not None
    assert moderacion.revisar(["llámame al 0424 971 9180"]) is not None
    assert moderacion.revisar(["hola"], prohibidos=["hola"]) is not None


def test_metadatos_sucios_no_se_anuncian():
    assert limpiar_pista("Karol G", "Provenza") == {"artista": "Karol G", "titulo": "Provenza"}
    assert limpiar_pista("DJ POOL", "Megamix 2025") is None


def test_pronunciacion_y_dominios():
    assert guion.para_voz("visita solisfmdigital.com") == "visita solisfmdigital punto com"
    assert guion.aplicar_pronunciacion("creamos radios", {"creamos": "hacemos"}) == "hacemos radios"


def test_aprobacion_por_defecto_y_personalizada(c):
    a = almacen.aprobacion(c)
    assert a["hora_clima"] == "auto" and a["noticia"] == "manual" and a["complacencia"] == "manual"
    almacen.guardar(c, "aprobacion", {"noticia": "auto", "corta": "manual", "basura": "x"})
    a = almacen.aprobacion(c)
    assert a["noticia"] == "auto" and a["corta"] == "manual" and "basura" not in a


def test_persona_al_aire_cruza_medianoche(c):
    almacen.guardar(c, "persona", {"nombre": "Base", "voz": "fish:aaa"})
    c.execute("INSERT INTO locutor_turnos (desde, hasta, nombre, voz) VALUES ('20:00', '02:00', 'Noche', 'fish:bbb')")
    c.execute("INSERT INTO locutor_turnos (desde, hasta, nombre, voz) VALUES ('08:00', '12:00', 'Día', 'fish:ccc')")
    assert almacen.persona_al_aire(c, "23:30")["nombre"] == "Noche"
    assert almacen.persona_al_aire(c, "01:00")["nombre"] == "Noche"
    assert almacen.persona_al_aire(c, "09:00")["voz"] == "fish:ccc"
    assert almacen.persona_al_aire(c, "15:00")["nombre"] == "Base"


def test_intervencion_y_estados(c):
    iid = almacen.crear_intervencion(c, "noticia", modo="manual", texto="Titular")
    assert c.execute("SELECT estado FROM locutor_intervenciones WHERE id=?", (iid,)).fetchone()[0] == "borrador"
    almacen.cambiar_estado(c, iid, "aprobada")
    almacen.cambiar_estado(c, iid, "lista", audio_id=7, duracion_seg=12.5)
    r = c.execute("SELECT * FROM locutor_intervenciones WHERE id=?", (iid,)).fetchone()
    assert r["estado"] == "lista" and r["audio_id"] == 7 and r["decidido_en"]
    with pytest.raises(ValueError):
        almacen.crear_intervencion(c, "otra")
    with pytest.raises(ValueError):
        almacen.cambiar_estado(c, iid, "volando")


def _origen(tmp_path):
    p = tmp_path / "locutor.db"
    o = sqlite3.connect(p)
    o.executescript("""
    CREATE TABLE clientes (id INTEGER PRIMARY KEY, slug TEXT, nombre TEXT, ciudad TEXT, genero TEXT,
        zona_horaria TEXT, modo TEXT, entrega_cifrada TEXT);
    CREATE TABLE personas_dj (cliente_id INTEGER, nombre TEXT, personalidad TEXT, muletillas TEXT, prohibidos TEXT,
        proveedor_voz TEXT, voz_id TEXT, pronunciacion TEXT);
    CREATE TABLE reglas (cliente_id INTEGER, intervenciones_por_hora INTEGER, tipos_permitidos TEXT, franjas TEXT,
        salidas TEXT, noticias_cada_min INTEGER, noticias_vetos TEXT, noticias_max_horas INTEGER, patron TEXT,
        cada_canciones INTEGER, cierre_complacencia TEXT);
    CREATE TABLE turnos (id INTEGER PRIMARY KEY, cliente_id INTEGER, desde TEXT, hasta TEXT, nombre TEXT,
        voz_id TEXT, personalidad TEXT);
    CREATE TABLE patrocinios (id INTEGER PRIMARY KEY, cliente_id INTEGER, texto TEXT, desde TEXT, hasta TEXT,
        frecuencia INTEGER, activo INTEGER, nombre TEXT);
    CREATE TABLE fuentes_noticias (id INTEGER PRIMARY KEY, cliente_id INTEGER, nombre TEXT, url TEXT, activo INTEGER,
        revisada_en TEXT, error TEXT, cierre TEXT);
    INSERT INTO clientes VALUES (1, 'solisfm', 'Solís FM Digital', 'Ciudad Guayana', '', 'America/Caracas', 'A', 'SECRETO-CIFRADO');
    INSERT INTO personas_dj VALUES (1, 'Britny', 'alegre', '[]', '["x"]', 'fish', '9ea79fe3', '{"creamos": "hacemos"}');
    INSERT INTO reglas VALUES (1, 60, '["corta","noticia"]', '[["08:00","20:00"]]', '{"hora_clima":"cortina"}', 45, '[]', 18,
        '["apertura","noticia"]', 3, 'Claro, {nombre}');
    INSERT INTO turnos VALUES (1, 1, '08:00', '12:00', 'Néstor', 'def180b1', 'serio');
    INSERT INTO patrocinios VALUES (1, 1, 'Texto del anuncio', '2026-10-11', '2026-12-31', 8, 1, 'Solís');
    INSERT INTO fuentes_noticias VALUES (1, 1, 'Primicia', 'https://primicia.com.ve/feed/', 1, NULL, NULL, 'Así lo reseña Primicia');
    """)
    o.commit()
    o.close()
    return p


def test_importar_la_misma_configuracion_sin_secretos(c, tmp_path):
    origen = _origen(tmp_path)
    r = almacen.importar_de_azuracast(c, origen)
    assert r == {"importado": True, "turnos": 1, "patrocinios": 1, "fuentes": 1, "persona": True, "reglas": True}
    assert almacen.emisora(c)["nombre"] == "Solís FM Digital"
    p = almacen.persona(c)
    assert p["voz"] == "fish:9ea79fe3" and p["pronunciacion"] == {"creamos": "hacemos"}
    assert almacen.reglas(c)["cada_canciones"] == 3 and almacen.reglas(c)["patron"] == ["apertura", "noticia"]
    assert c.execute("SELECT voz FROM locutor_turnos").fetchone()[0] == "fish:def180b1"
    assert c.execute("SELECT cierre FROM locutor_fuentes").fetchone()[0] == "Así lo reseña Primicia"
    volcado = json.dumps([[str(x) for x in fila] for t in ("locutor_config", "locutor_turnos")
                          for fila in c.execute(f"SELECT * FROM {t}")])
    assert "SECRETO" not in volcado
    # segunda vez: no duplica
    assert almacen.importar_de_azuracast(c, origen)["importado"] is False
    assert c.execute("SELECT COUNT(*) FROM locutor_turnos").fetchone()[0] == 1
    assert almacen.importar_de_azuracast(c, origen, reemplazar=True)["importado"] is True
    assert c.execute("SELECT COUNT(*) FROM locutor_turnos").fetchone()[0] == 1
