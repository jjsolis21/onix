"""Locutor IA: datos verificados de artistas y canciones (fuentes falsas, sin internet)."""

import pytest

from backend.ia import proveedores
from backend.locutor import almacen, datos, servicio as svc
from test_locutor_servicio import AHORA

WIKI_BUSQUEDA = {"query": {"search": [{"title": "Danny Ocean"}, {"title": "Me rehúso"}]}}
WIKI_RESUMEN = {"type": "standard", "description": "cantante y productor venezolano",
                "extract": "Daniel Alejandro Morales Reyes (Caracas, 5 de mayo de 1992), conocido por su nombre artístico "
                           "Danny Ocean, es un cantante y compositor venezolano. Saltó a la fama con «Me rehúso». "
                           "Tercera frase que ya no cabe."}
MB = {"recordings": [
    {"score": 100, "title": "Me rehúso", "first-release-date": "2017-06-16", "artist-credit": [{"name": "Danny Ocean"}]},
    {"score": 99, "title": "Me Rehúso", "first-release-date": "2026-07-22", "artist-credit": [{"name": "Danny Ocean"}]},
    {"score": 100, "title": "Me rehúso", "first-release-date": "2010", "artist-credit": [{"name": "Otro Artista"}]},
    {"score": 60, "title": "Me rehúso", "first-release-date": "1999", "artist-credit": [{"name": "Danny Ocean"}]}]}


def fuente(url):
    if "w/api.php" in url:
        return WIKI_BUSQUEDA
    if "page/summary" in url:
        return WIKI_RESUMEN
    if "musicbrainz" in url:
        return MB
    raise AssertionError(url)


def pagina(titulo, tipo, descripcion, extracto):
    """Fuente falsa de Wikipedia con una sola página."""
    def get(url):
        if "w/api.php" in url:
            return {"query": {"search": [{"title": titulo}]}}
        return {"type": tipo, "description": descripcion, "extract": extracto}
    return get


@pytest.fixture
def c(tmp_path):
    con = almacen.conectar(tmp_path / "d.db")
    yield con
    con.close()


def test_artista_solo_si_la_pagina_es_suya_y_habla_de_musica():
    d = datos.buscar_artista("Danny Ocean ft. Otro", fuente)
    assert d.startswith("Daniel Alejandro Morales Reyes, conocido") and "Caracas" not in d     # sin el paréntesis
    assert "Saltó a la fama" in d and "Tercera frase" not in d                                  # dos frases
    # «A.CINCO» no es «Cinco Días»
    assert datos.buscar_artista("A.CINCO", pagina("Cinco Días", "standard", "diario económico español",
                                                  "Cinco Días es un diario.")) is None
    # la página se llama igual pero no es de música
    assert datos.buscar_artista("Fenix", pagina("Fenix", "standard", "ave mitológica", "El fénix es un ave.")) is None
    # página de desambiguación
    assert datos.buscar_artista("Maná", pagina("Maná", "disambiguation", "", "Maná puede ser una banda.")) is None


def test_anio_el_primer_lanzamiento_del_mismo_artista():
    assert datos.buscar_anio("Danny Ocean", "Me Rehúso (Remix)", fuente) == 2017
    assert datos.buscar_anio("Danny Ocean", "Otra Cosa", fuente) is None
    assert datos.buscar_anio("", "x", fuente) is None


def test_se_busca_una_sola_vez_y_lo_manual_manda(c):
    llamadas = []

    def contar(url):
        llamadas.append(url)
        return fuente(url)
    d1 = datos.datos_para(c, "Danny Ocean", "Me Rehúso", None, get=contar)
    assert len(d1) == 2 and "2017" in d1[1] and "Danny Ocean" in d1[0]
    n = len(llamadas)
    assert datos.datos_para(c, "DANNY OCEAN", "me rehuso", None, get=contar) == d1      # de la base, sin internet
    assert len(llamadas) == n
    # la emisora escribe su propio dato del artista y descarta el de la canción
    datos.guardar(c, "artista", datos.norm("Danny Ocean"), "Danny Ocean", "Danny Ocean es de Caracas.", "manual")
    datos.guardar(c, "cancion", "danny ocean|me rehuso", "Danny Ocean - Me Rehúso", "", "manual", "descartado")
    assert datos.datos_para(c, "Danny Ocean", "Me Rehúso", None, get=contar) == ["Danny Ocean es de Caracas."]
    assert len(llamadas) == n


def test_sin_coincidencia_no_guarda_dato_ni_insiste(c):
    llamadas = []

    def nada(url):
        llamadas.append(url)
        return {"query": {"search": []}, "recordings": []}
    assert datos.datos_para(c, "Grupo Rarísimo", "Tema", None, get=nada) == []
    n = len(llamadas)
    assert datos.datos_para(c, "Grupo Rarísimo", "Tema", None, get=nada) == []
    assert len(llamadas) == n
    assert c.execute("SELECT COUNT(*) FROM locutor_datos WHERE estado = 'sin_datos'").fetchone()[0] == 2


def test_el_anio_de_la_biblioteca_va_antes_que_internet(c):
    def solo_wikipedia(url):
        assert "musicbrainz" not in url
        return fuente(url)
    d = datos.datos_para(c, "Danny Ocean", "Me Rehúso", "2017-06-16", get=solo_wikipedia)
    assert any("2017" in x for x in d)


def test_red_caida_no_rompe_ni_guarda_basura(c):
    def caida(url):
        raise OSError("sin red")
    assert datos.datos_para(c, "Danny Ocean", "Me Rehúso", None, get=caida) == []
    assert c.execute("SELECT COUNT(*) FROM locutor_datos").fetchone()[0] == 0


def _solo_danny(con, artista, titulo, anio):
    return ["La canción «Me Rehúso» se lanzó en el año 2017."] if artista == "Danny Ocean" else []


def test_con_datos_la_entrada_pasa_a_contenido_y_pide_aprobacion(c, monkeypatch):
    almacen.guardar(c, "emisora", {"nombre": "Solís FM Digital", "ciudad": "Ciudad Guayana"})
    almacen.guardar(c, "persona", {"nombre": "Britny", "voz": "Kore"})
    c.commit()
    visto = {}

    def ia(prompt, sistema="", db_path=None):
        visto["prompt"] = prompt
        return {"texto": "Escuchabas a Danny Ocean con Me Rehúso, canción lanzada en dos mil diecisiete. "
                         "Ahora, Feid con Normal. Estás en sintonía de Solís FM Digital.", "proveedor": "gemini"}
    monkeypatch.setattr(proveedores, "generar_texto", ia)
    r = svc.preparar(c, "corta", {"artista": "Danny Ocean", "titulo": "Me Rehúso"},
                     {"artista": "Feid", "titulo": "Normal"}, AHORA, buscar_datos=_solo_danny)
    assert r["tipo"] == "contenido" and r["estado"] == "borrador"          # con dato: pide aprobación
    assert "Dato verificado de la canción que sonó" in visto["prompt"] and "2017" in visto["prompt"]
    assert "sintonía" in r["texto"]


def test_la_ia_no_puede_colar_un_dato_que_no_esta_guardado(c, monkeypatch):
    almacen.guardar(c, "persona", {"nombre": "Britny", "voz": "Kore"})
    c.commit()
    monkeypatch.setattr(proveedores, "generar_texto", lambda *a, **k: {
        "texto": "Sonó Danny Ocean con Me Rehúso, ganadora de un Grammy en 2019. Ahora Feid con Normal.",
        "proveedor": "gemini"})
    r = svc.preparar(c, "corta", {"artista": "Danny Ocean", "titulo": "Me Rehúso"},
                     {"artista": "Feid", "titulo": "Normal"}, AHORA, buscar_datos=_solo_danny)
    assert "Grammy" not in r["texto"] and "2019" not in r["texto"]          # cae a la plantilla, que no inventa


def test_sin_datos_sigue_siendo_la_corta_automatica(c, monkeypatch):
    almacen.guardar(c, "persona", {"nombre": "Britny", "voz": "Kore"})
    c.commit()
    monkeypatch.setattr(proveedores, "generar_texto",
                        lambda *a, **k: {"texto": "Sigue Feid con Normal.", "proveedor": "gemini"})
    r = svc.preparar(c, "corta", {"artista": "Karol G", "titulo": "Provenza"},
                     {"artista": "Feid", "titulo": "Normal"}, AHORA, buscar_datos=lambda *a: [])
    assert r["tipo"] == "corta" and r["estado"] == "aprobada"
