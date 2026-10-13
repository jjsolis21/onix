"""Voice Tracker: grabar, texto con IA (o plantilla) y voz con IA, sin salir a internet."""

import base64
import io
import wave

import pytest
from fastapi.testclient import TestClient

from backend.api.auth import permiso
from backend.ia import proveedores
from test_relojes_api import subir


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def sin_claves(client):
    for k in client.get("/api/v1/ia/claves").json():
        client.delete(f"/api/v1/ia/claves/{k['id']}")
    proveedores._fallos.clear()
    yield


def _audio(aid):
    import sqlite3
    from backend.paths import DB_PATH
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row
    try:
        return dict(c.execute("SELECT * FROM audios WHERE id = ?", (aid,)).fetchone())
    finally:
        c.close()


def _wav(seg=2.0, hz=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(hz)
        w.writeframes(b"\x00\x01" * int(seg * hz))
    return buf.getvalue()


def test_grabar_crea_una_locucion_que_no_es_musica(client):
    r = client.post("/api/v1/voicetrack/grabar", files={"file": ("vt.wav", _wav(), "audio/wav")},
                    data={"titulo": "Saludo de la tarde", "locutor": "Jose"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["duracion"] == 2.0 and d["archivo_path"].startswith("VOCES/")
    assert any(v["id"] == d["audio_id"] for v in client.get("/api/v1/voicetrack").json())
    a = _audio(d["audio_id"])
    assert a["tipo_audio"] == "Locucion" and a["artista"] == "Jose"
    # no es un WAV, o muy corto
    assert client.post("/api/v1/voicetrack/grabar", files={"file": ("x.wav", b"hola", "audio/wav")}).status_code == 415
    assert client.post("/api/v1/voicetrack/grabar", files={"file": ("x.wav", _wav(0.2), "audio/wav")}).status_code == 422
    assert client.delete(f"/api/v1/voicetrack/{d['audio_id']}").status_code == 200
    assert not any(v["id"] == d["audio_id"] for v in client.get("/api/v1/voicetrack").json())


def test_texto_sin_claves_usa_la_plantilla(client, tmp_path):
    a = subir(client, tmp_path, "BAILANDO")
    b = subir(client, tmp_path, "DESPACITO")
    r = client.post("/api/v1/voicetrack/texto", json={"anterior_id": a, "siguiente_id": b}).json()
    assert r["origen"] == "plantilla" and "BAILANDO" in r["texto"] and "DESPACITO" in r["texto"]


def test_texto_con_ia(client, tmp_path, monkeypatch):
    client.post("/api/v1/ia/claves", json={"nombre": "G", "proveedor": "gemini", "clave": "K-123456789"})
    a = subir(client, tmp_path, "TEMA X")
    pedidos = []

    def falso(url, cab, cuerpo, tiempo=20):
        pedidos.append(cuerpo)
        return {"candidates": [{"content": {"parts": [{"text": "¡Qué temazo!   Seguimos con más."}]}}]}
    monkeypatch.setattr(proveedores, "http_post", falso)
    r = client.post("/api/v1/voicetrack/texto", json={"anterior_id": a, "indicacion": "saluda a Caracas"}).json()
    assert r["origen"] == "ia" and r["texto"] == "¡Qué temazo! Seguimos con más."
    pedido = pedidos[0]["contents"][0]["parts"][0]["text"]
    assert "TEMA X" in pedido and "Caracas" in pedido


def test_voz_con_ia_gemini(client, monkeypatch):
    assert client.post("/api/v1/voicetrack/ia", json={"texto": "Hola Caracas"}).status_code == 503   # sin claves
    client.post("/api/v1/ia/claves", json={"nombre": "G", "proveedor": "gemini", "clave": "K-123456789"})
    assert any(v["voz"] == "Kore" for v in client.get("/api/v1/voicetrack/voces").json()["voces"])
    pcm = b"\x00\x02" * 24000 * 2                                   # 2 s a 24 kHz

    def falso(url, cab, cuerpo, tiempo=20):
        assert "tts" in url and cuerpo["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Puck"
        return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16", "data": base64.b64encode(pcm).decode()}}]}}]}
    monkeypatch.setattr(proveedores, "http_post", falso)
    r = client.post("/api/v1/voicetrack/ia", json={"texto": "Hola Caracas", "voz": "Puck"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["proveedor"] == "gemini" and d["duracion"] == 2.0
    assert _audio(d["audio_id"])["artista"] == "LOCUTOR IA"


def test_voz_respaldo_openai_si_gemini_falla(client, monkeypatch):
    client.post("/api/v1/ia/claves", json={"nombre": "G", "proveedor": "gemini", "clave": "K-123456789"})
    client.post("/api/v1/ia/claves", json={"nombre": "O", "proveedor": "openai", "clave": "sk-987654321"})
    monkeypatch.setattr(proveedores, "http_post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 429: cuota")))
    monkeypatch.setattr(proveedores, "http_post_bytes", lambda url, cab, cuerpo, tiempo=60: _wav(1.5))
    r = client.post("/api/v1/voicetrack/ia", json={"texto": "Respaldo"})
    assert r.status_code == 201 and r.json()["proveedor"] == "openai"


def test_operador_puede_usar_el_voice_tracker():
    assert permiso("operador", "POST", "/api/v1/voicetrack/grabar")
    assert permiso("programacion", "POST", "/api/v1/voicetrack/ia")
    assert not permiso("operador", "GET", "/api/v1/ia/claves")
