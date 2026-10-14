"""
proveedores.py — Ónix FM
Generación de texto con varias IA y respaldo automático: se prueban las claves activas en su orden y,
si una falla (cuota, red, clave mala), se pasa a la siguiente. Si fallan todas, `generar_texto` lanza
IAError y quien llama usa su plantilla sin IA (nada que salga al aire depende de que la IA responda).

Proveedores:
  gemini  → API de Google (generativelanguage), clave en la cabecera x-goog-api-key.
  openai  → cualquier API compatible con «chat/completions» (OpenAI, Groq, OpenRouter, Together…);
            se indica la dirección base.
"""

import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from backend.paths import DB_PATH

PROVEEDORES = {
    "gemini": {"nombre": "Google Gemini", "modelo": "gemini-3.8-flash", "base_url": ""},
    "openai": {"nombre": "OpenAI", "modelo": "gpt-4o-mini", "base_url": "https://api.openai.com/v1"},
    "groq": {"nombre": "Groq", "modelo": "llama-3.3-70b-versatile", "base_url": "https://api.groq.com/openai/v1"},
    "openrouter": {"nombre": "OpenRouter", "modelo": "", "base_url": "https://openrouter.ai/api/v1"},
    "otro": {"nombre": "Otro (compatible con OpenAI)", "modelo": "", "base_url": ""},
    # Solo voz (no escribe textos): voces clonadas o de su catálogo, por su id
    "fish": {"nombre": "Fish Audio (solo voz)", "modelo": "s2.1-pro", "base_url": "https://api.fish.audio"},
}
SOLO_VOZ = ("fish",)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
ESPERA_TRAS_FALLO = 300          # segundos que una clave fallida se salta mientras haya otras
TIEMPO_LIMITE = 20               # segundos por intento


class IAError(Exception):
    """Ninguna clave pudo responder."""


_fallos: dict[int, float] = {}   # id de clave → momento (monotonic) de su último fallo
MODELOS_GEMINI = (PROVEEDORES["gemini"]["modelo"], "gemini-3.5-flash")   # el vigente y su anterior
_gemini_ok: dict = {}            # id de clave → último modelo de texto que le respondió
PAUSAS_503 = (2, 4, 8)           # segundos entre reintentos cuando Gemini responde «alta demanda»


def _detalle_http(e: "urllib.error.HTTPError") -> str:
    """El mensaje del proveedor: viene en {"error": {"message"}} (Gemini, OpenAI) o en {"message"} (Fish Audio)."""
    try:
        crudo = e.read().decode("utf-8", "replace")
    except Exception:
        return ""
    try:
        d = json.loads(crudo)
        err = d.get("error", d.get("message", d.get("detail", "")))
        texto = err.get("message", "") if isinstance(err, dict) else str(err)
    except Exception:
        texto = crudo
    return " ".join(str(texto).split())[:200]


def http_post(url: str, cabeceras: dict, cuerpo: dict, tiempo: int = TIEMPO_LIMITE) -> dict:
    """POST JSON → JSON. Los tests lo sustituyen para no salir a internet."""
    req = urllib.request.Request(url, data=json.dumps(cuerpo).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **cabeceras}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=tiempo) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {_detalle_http(e)}".strip())
    except Exception as e:                       # red caída, tiempo agotado, JSON roto…
        raise RuntimeError(str(e) or e.__class__.__name__)


def _llamar(k: dict, prompt: str, sistema: str) -> str:
    modelo = k["modelo"] or PROVEEDORES.get(k["proveedor"], {}).get("modelo", "")
    if k["proveedor"] == "gemini":
        cuerpo = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if sistema:
            cuerpo["systemInstruction"] = {"parts": [{"text": sistema}]}
        # Sin modelo propio en la clave: se prueban los vigentes y se recuerda el que respondió. Un modelo que se
        # agota (cuota 429), se satura (503), se retira (404) o no contesta a tiempo cede al siguiente.
        propio = (k.get("modelo") or "").strip()
        if propio:
            modelos = [propio]
        else:
            modelos = list(MODELOS_GEMINI)
            previo = _gemini_ok.get(k.get("id"))
            if previo in modelos:
                modelos.remove(previo)
                modelos.insert(0, previo)
        d, ultimo = None, None
        for m in modelos:
            for intento in range(PAUSAS_503 and len(PAUSAS_503) + 1):
                try:
                    d = http_post(GEMINI_URL.format(modelo=m), {"x-goog-api-key": k["clave"]}, cuerpo)
                    if not propio:
                        _gemini_ok[k.get("id")] = m
                    break
                except RuntimeError as e:
                    ultimo = e
                    if str(e).startswith("HTTP 503") and intento < len(PAUSAS_503):
                        time.sleep(PAUSAS_503[intento])      # «alta demanda»: suele ser cosa de segundos
                        continue
                    if not (str(e).startswith(("HTTP 404", "HTTP 429", "HTTP 503", "HTTP 500")) or "timed out" in str(e)):
                        raise
                    break
            if d is not None:
                break
        if d is None:
            raise ultimo
        partes = (((d.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        texto = "".join(p.get("text", "") for p in partes).strip()
    else:
        base = (k["base_url"] or PROVEEDORES.get(k["proveedor"], {}).get("base_url", "")).rstrip("/")
        if not base or not modelo:
            raise RuntimeError("Falta la dirección base o el modelo")
        msgs = ([{"role": "system", "content": sistema}] if sistema else []) + [{"role": "user", "content": prompt}]
        d = http_post(f"{base}/chat/completions", {"Authorization": f"Bearer {k['clave']}"},
                      {"model": modelo, "messages": msgs})
        texto = (((d.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not texto:
        raise RuntimeError("La IA respondió vacío")
    return texto


def _limpio(msg: str, clave: str) -> str:
    """El error se guarda y se muestra: nunca debe llevar la clave."""
    return (msg.replace(clave, "***") if clave else msg)[:300]


def _marcar(conn, k: dict, error: Optional[str]):
    ahora = datetime.now().isoformat(timespec="seconds")
    if error is None:
        _fallos.pop(k["id"], None)
        conn.execute("UPDATE ia_claves SET ultimo_ok = ?, ultimo_error = NULL, ultimo_error_en = NULL WHERE id = ?", (ahora, k["id"]))
    else:
        _fallos[k["id"]] = time.monotonic()
        conn.execute("UPDATE ia_claves SET ultimo_error = ?, ultimo_error_en = ? WHERE id = ?", (error, ahora, k["id"]))
    conn.commit()


def probar(k: dict) -> dict:
    """Prueba una clave concreta (botón «Probar»). No toca el orden ni el enfriamiento."""
    t0 = time.monotonic()
    try:
        if k["proveedor"] in SOLO_VOZ:
            v = voces_de(k)
            _voz_con(k, "Prueba.", f"fish:{v[0][1]}" if v else "", "")
        else:
            _llamar(k, "Responde solo con la palabra OK.", "")
        return {"ok": True, "ms": int((time.monotonic() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "error": _limpio(str(e), k["clave"])}


def generar_texto(prompt: str, sistema: str = "", db_path: str = DB_PATH) -> dict:
    """
    Devuelve {"texto", "clave_id", "proveedor", "intentos": [{"id","nombre","error"}]} con la primera
    clave que responda. Las que fallaron hace poco se saltan, salvo que no quede otra.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        claves = [dict(r) for r in conn.execute(
            "SELECT * FROM ia_claves WHERE activo = 1 AND proveedor NOT IN ('fish') ORDER BY orden, id")]
        if not claves:
            raise IAError("No hay claves de IA activas para escribir textos")
        ahora = time.monotonic()
        sanas = [k for k in claves if ahora - _fallos.get(k["id"], -1e9) > ESPERA_TRAS_FALLO]
        intentos = []
        for k in (sanas or claves):
            try:
                texto = _llamar(k, prompt, sistema)
            except Exception as e:
                err = _limpio(str(e), k["clave"])
                _marcar(conn, k, err)
                intentos.append({"id": k["id"], "nombre": k["nombre"], "error": err})
                continue
            _marcar(conn, k, None)
            return {"texto": texto, "clave_id": k["id"], "proveedor": k["proveedor"], "intentos": intentos}
        raise IAError("Ninguna IA respondió: " + " | ".join(f"{i['nombre']}: {i['error']}" for i in intentos))
    finally:
        conn.close()


# ── Voz (texto → audio) ─────────────────────────────────────────────────────
# Solo Gemini y OpenAI tienen voz; Groq / OpenRouter / otros se saltan.
GEMINI_TTS_MODELO = "gemini-2.5-flash-preview-tts"
GEMINI_TTS_HZ = 24000                  # Gemini devuelve PCM 16 bits mono a 24 kHz
VOCES = {
    "gemini": ["Kore", "Puck", "Charon", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"],
    "openai": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
}


def http_post_bytes(url: str, cabeceras: dict, cuerpo: dict, tiempo: int = 60) -> bytes:
    """POST JSON → bytes (audio). Los tests lo sustituyen."""
    req = urllib.request.Request(url, data=json.dumps(cuerpo).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **cabeceras}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=tiempo) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {_detalle_http(e)}".strip())
    except Exception as e:
        raise RuntimeError(str(e) or e.__class__.__name__)


def pcm_a_wav(pcm: bytes, hz: int = GEMINI_TTS_HZ) -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(hz)
        w.writeframes(pcm)
    return buf.getvalue()


def voces_de(k: dict) -> list[tuple[str, str]]:
    """Voces propias de una clave: "Britny=593f…, Dag=629d…" → [("Britny", "593f…"), …]"""
    out = []
    for parte in re.split(r"[,;\n]+", k.get("voces") or ""):
        if "=" in parte:
            nombre, vid = (x.strip() for x in parte.split("=", 1))
            if nombre and re.fullmatch(r"[A-Za-z0-9_-]{6,64}", vid):
                out.append((nombre, vid))
    return out


def _voz_con(k: dict, texto: str, voz: str, estilo: str) -> bytes:
    import base64
    if k["proveedor"] == "fish":
        ids = [vid for _, vid in voces_de(k)]
        # Una voz pedida como «fish:ID» (turnos del Locutor) se usa tal cual; la lista de la clave es solo un atajo
        vid = voz[5:] if voz.startswith("fish:") and voz[5:] else (ids[0] if ids else "")
        if not vid:
            raise RuntimeError("Fish Audio: no se indicó la voz y esta clave no tiene voces cargadas")
        base = (k["base_url"] or PROVEEDORES["fish"]["base_url"]).rstrip("/")
        audio = http_post_bytes(f"{base}/v1/tts", {"Authorization": f"Bearer {k['clave']}",
                                                  "model": k["modelo"] or PROVEEDORES["fish"]["modelo"]},
                                {"text": texto, "reference_id": vid, "format": "wav", "normalize": True})
        if not audio.startswith(b"RIFF"):
            raise RuntimeError("Fish Audio no devolvió un WAV")
        return audio
    if k["proveedor"] == "gemini":
        voz = voz if voz in VOCES["gemini"] else VOCES["gemini"][0]
        prompt = f"{estilo.strip()}: {texto}" if estilo.strip() else texto
        d = http_post(GEMINI_URL.format(modelo=GEMINI_TTS_MODELO), {"x-goog-api-key": k["clave"]}, {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voz}}}},
        }, tiempo=60)
        partes = (((d.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        datos = next((p["inlineData"]["data"] for p in partes if p.get("inlineData", {}).get("data")), None)
        if not datos:
            raise RuntimeError("Gemini no devolvió audio")
        return pcm_a_wav(base64.b64decode(datos))
    base = (k["base_url"] or PROVEEDORES["openai"]["base_url"]).rstrip("/")
    voz = voz if voz in VOCES["openai"] else VOCES["openai"][0]
    cuerpo = {"model": "gpt-4o-mini-tts", "input": texto, "voice": voz, "response_format": "wav"}
    if estilo.strip():
        cuerpo["instructions"] = estilo.strip()
    audio = http_post_bytes(f"{base}/audio/speech", {"Authorization": f"Bearer {k['clave']}"}, cuerpo)
    if not audio.startswith(b"RIFF"):
        raise RuntimeError("La voz no devolvió un WAV")
    return audio


def generar_voz(texto: str, voz: str = "", estilo: str = "", db_path: str = DB_PATH) -> dict:
    """
    Texto → WAV con la primera clave con voz que responda (Gemini u OpenAI), en el orden de la sección IA.
    Devuelve {"wav": bytes, "proveedor", "clave_id", "intentos"}. Sin ninguna: IAError.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        claves = [dict(r) for r in conn.execute(
            "SELECT * FROM ia_claves WHERE activo = 1 AND proveedor IN ('gemini', 'openai', 'fish') ORDER BY orden, id")]
        if not claves:
            raise IAError("No hay claves de IA con voz (Fish Audio, Gemini u OpenAI) activas")
        # Una voz elegida de un proveedor va primero con ese proveedor
        prefer = ("fish" if voz.startswith("fish:") else "gemini" if voz in VOCES["gemini"]
                  else "openai" if voz in VOCES["openai"] else None)
        if prefer:
            claves.sort(key=lambda k: k["proveedor"] != prefer)
        intentos = []
        for k in claves:
            try:
                wav = _voz_con(k, texto, voz, estilo)
            except Exception as e:
                err = _limpio(str(e), k["clave"])
                _marcar(conn, k, err)
                intentos.append({"id": k["id"], "nombre": k["nombre"], "error": err})
                continue
            _marcar(conn, k, None)
            return {"wav": wav, "proveedor": k["proveedor"], "clave_id": k["id"], "intentos": intentos}
        raise IAError("Ninguna voz respondió: " + " | ".join(f"{i['nombre']}: {i['error']}" for i in intentos))
    finally:
        conn.close()
