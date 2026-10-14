"""Cartuchera como Soho: cargar un efecto desde un archivo y editar el botón (título, color, marcas, volumen)."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from conftest import make_wav


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def paleta(client):
    clave = client.post("/api/v1/paletas", json={"nombre": "Efectos prueba", "slots": 9}).json()["clave"]
    yield clave
    client.delete(f"/api/v1/paletas/{clave}")


def _wav_bytes(tmp_path, seg=4.0):
    p = make_wav(tmp_path / "claps.wav", seconds=seg)
    return p.read_bytes()


def _boton(client, banco, slot):
    return next((s for s in client.get("/api/v1/cartuchera").json()["slots"][banco] if s["slot"] == slot), None)


def test_cargar_efecto_desde_archivo_queda_en_el_boton(client, paleta, tmp_path):
    r = client.post(f"/api/v1/cartuchera/{paleta}/2/archivo", files={"file": ("Claps Públicos.wav", _wav_bytes(tmp_path), "audio/wav")},
                    data={"etiqueta": "CLAPS"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["duracion"] == 4 and d["archivo_path"].startswith("library/efectos/")
    b = _boton(client, paleta, 2)
    assert b["audio_id"] == d["audio_id"] and b["etiqueta"] == "CLAPS" and b["tipo_audio"] == "Efecto"
    assert b["volumen"] == 100 and b["mark_in"] is None and b["color"] == ""
    # el efecto queda en la Biblioteca como «Efecto», no como música
    from backend.paths import DB_PATH
    c = sqlite3.connect(DB_PATH)
    assert c.execute("SELECT tipo_audio, artista FROM audios WHERE id = ?", (d["audio_id"],)).fetchone() == ("Efecto", "EFECTO")


def test_archivo_que_no_es_audio_se_rechaza(client, paleta):
    assert client.post(f"/api/v1/cartuchera/{paleta}/1/archivo", files={"file": ("nota.txt", b"hola", "text/plain")}).status_code == 415
    assert client.post(f"/api/v1/cartuchera/{paleta}/1/archivo", files={"file": ("roto.wav", b"no es audio", "audio/wav")}).status_code == 422
    assert client.post(f"/api/v1/cartuchera/{paleta}/99/archivo", files={"file": ("a.wav", b"RIFF", "audio/wav")}).status_code == 404


def test_editar_titulo_color_marcas_y_volumen(client, paleta, tmp_path):
    client.post(f"/api/v1/cartuchera/{paleta}/1/archivo", files={"file": ("e.wav", _wav_bytes(tmp_path), "audio/wav")})
    r = client.patch(f"/api/v1/cartuchera/{paleta}/1", json={"etiqueta": "APLAUSOS", "color": "#7a0a0a",
                                                              "mark_in": 0.5, "mark_out": 3.0, "volumen": 80})
    assert r.status_code == 200, r.text
    b = _boton(client, paleta, 1)
    assert (b["etiqueta"], b["color"], b["mark_in"], b["mark_out"], b["volumen"]) == ("APLAUSOS", "#7a0a0a", 0.5, 3.0, 80)
    # validaciones
    assert client.patch(f"/api/v1/cartuchera/{paleta}/1", json={"mark_in": 3.0, "mark_out": 1.0}).status_code == 422
    assert client.patch(f"/api/v1/cartuchera/{paleta}/1", json={"mark_in": 9.0, "mark_out": 10.0}).status_code == 422
    assert client.patch(f"/api/v1/cartuchera/{paleta}/1", json={"color": "rojo"}).status_code == 422
    assert client.patch(f"/api/v1/cartuchera/{paleta}/1", json={"volumen": 400}).status_code == 422
    # quitar marcas: todo el archivo otra vez
    client.patch(f"/api/v1/cartuchera/{paleta}/1", json={"quitar_marcas": True})
    b = _boton(client, paleta, 1)
    assert b["mark_in"] is None and b["mark_out"] is None and b["etiqueta"] == "APLAUSOS"
    # botón vacío
    assert client.patch(f"/api/v1/cartuchera/{paleta}/5", json={"etiqueta": "x"}).status_code == 404


def test_cambiar_el_audio_por_arrastre_reinicia_las_marcas(client, paleta, tmp_path):
    client.post(f"/api/v1/cartuchera/{paleta}/3/archivo", files={"file": ("a.wav", _wav_bytes(tmp_path), "audio/wav")})
    client.patch(f"/api/v1/cartuchera/{paleta}/3", json={"mark_in": 1.0, "mark_out": 2.0})
    otro = client.post(f"/api/v1/cartuchera/{paleta}/4/archivo",
                       files={"file": ("b.wav", _wav_bytes(tmp_path, 3.0), "audio/wav")}).json()["audio_id"]
    assert client.put(f"/api/v1/cartuchera/{paleta}/3", json={"audio_id": otro}).status_code == 200
    b = _boton(client, paleta, 3)
    assert b["audio_id"] == otro and b["mark_in"] is None and b["mark_out"] is None


def test_al_disparar_el_motor_recibe_las_marcas_y_el_volumen_del_boton(client, paleta, tmp_path):
    from backend.api import api_server
    aid = client.post(f"/api/v1/cartuchera/{paleta}/1/archivo",
                      files={"file": ("e.wav", _wav_bytes(tmp_path), "audio/wav")}).json()["audio_id"]
    client.patch(f"/api/v1/cartuchera/{paleta}/1", json={"mark_in": 0.5, "mark_out": 3.0, "volumen": 70})
    op = api_server._opciones_boton({"audio_id": aid, "banco": paleta, "slot": 1})
    assert op == {"boton": f"{paleta}/1", "mark_in": 0.5, "mark_out": 3.0, "volumen": 70}
    assert api_server._opciones_boton({"audio_id": aid}) == {}                     # atajo sin botón: como antes
    # el botón ya tiene otro audio: no se le aplican marcas ajenas
    assert api_server._opciones_boton({"audio_id": aid + 999, "banco": paleta, "slot": 1}) == {"boton": f"{paleta}/1"}
    visto = {}
    real = api_server.engine.fire_effect
    api_server.engine.fire_effect = lambda a, c=0, o=None: visto.update(a=a, c=c, o=o) or True
    try:
        api_server._run_player_command("fire_effect", {"audio_id": aid, "canal_offset": 0, "banco": paleta, "slot": 1})
    finally:
        api_server.engine.fire_effect = real
    assert visto["a"] == aid and visto["o"]["mark_out"] == 3.0 and visto["o"]["boton"] == f"{paleta}/1"


def test_cargar_y_editar_avisan_al_studio_por_websocket(client, paleta, tmp_path):
    """El Studio se entera solo (sin recargar): cada cambio emite `cartuchera_updated` por el WebSocket del servidor."""
    with client.websocket_connect("/ws") as ws:
        def esperar(evento):
            for _ in range(30):
                m = ws.receive_json()
                if m.get("event") == evento:
                    return m
            raise AssertionError(f"no llegó {evento}")
        client.post(f"/api/v1/cartuchera/{paleta}/2/archivo", files={"file": ("x.wav", _wav_bytes(tmp_path), "audio/wav")})
        assert esperar("cartuchera_updated")["data"] == {"banco": paleta, "slot": 2}
        client.patch(f"/api/v1/cartuchera/{paleta}/2", json={"color": "#0050c8"})
        assert esperar("cartuchera_updated")["data"]["slot"] == 2


def test_los_routers_no_importan_api_server_por_otro_nombre():
    """El servidor arranca como «api_server:app»; importarlo como backend.api.api_server crearía otra copia
    (otro WebSocket sin clientes y otro motor), y los avisos no llegarían al Studio."""
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent / "backend"
    for f in [raiz / "api" / "cartuchera.py", raiz / "api" / "voicetrack.py", raiz / "api" / "locutor.py",
              *(raiz / "locutor").glob("*.py")]:
        codigo = "\n".join(l for l in f.read_text(encoding="utf-8").splitlines() if not l.strip().startswith("#"))
        assert "import api_server" not in codigo, f.name
