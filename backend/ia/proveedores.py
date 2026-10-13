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
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from backend.paths import DB_PATH

PROVEEDORES = {
    "gemini": {"nombre": "Google Gemini", "modelo": "gemini-2.5-flash", "base_url": ""},
    "openai": {"nombre": "OpenAI", "modelo": "gpt-4o-mini", "base_url": "https://api.openai.com/v1"},
    "groq": {"nombre": "Groq", "modelo": "llama-3.3-70b-versatile", "base_url": "https://api.groq.com/openai/v1"},
    "openrouter": {"nombre": "OpenRouter", "modelo": "", "base_url": "https://openrouter.ai/api/v1"},
    "otro": {"nombre": "Otro (compatible con OpenAI)", "modelo": "", "base_url": ""},
}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
ESPERA_TRAS_FALLO = 300          # segundos que una clave fallida se salta mientras haya otras
TIEMPO_LIMITE = 20               # segundos por intento


class IAError(Exception):
    """Ninguna clave pudo responder."""


_fallos: dict[int, float] = {}   # id de clave → momento (monotonic) de su último fallo


def http_post(url: str, cabeceras: dict, cuerpo: dict, tiempo: int = TIEMPO_LIMITE) -> dict:
    """POST JSON → JSON. Los tests lo sustituyen para no salir a internet."""
    req = urllib.request.Request(url, data=json.dumps(cuerpo).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **cabeceras}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=tiempo) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detalle = ""
        try:
            crudo = e.read().decode("utf-8", "replace")
            try:                                  # los mensajes útiles vienen en {"error": {"message": …}}
                err = json.loads(crudo).get("error", {})
                detalle = err.get("message", "") if isinstance(err, dict) else str(err)
            except Exception:
                detalle = crudo
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {' '.join(detalle.split())[:200]}".strip())
    except Exception as e:                       # red caída, tiempo agotado, JSON roto…
        raise RuntimeError(str(e) or e.__class__.__name__)


def _llamar(k: dict, prompt: str, sistema: str) -> str:
    modelo = k["modelo"] or PROVEEDORES.get(k["proveedor"], {}).get("modelo", "")
    if k["proveedor"] == "gemini":
        cuerpo = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if sistema:
            cuerpo["systemInstruction"] = {"parts": [{"text": sistema}]}
        d = http_post(GEMINI_URL.format(modelo=modelo or "gemini-2.5-flash"), {"x-goog-api-key": k["clave"]}, cuerpo)
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
        claves = [dict(r) for r in conn.execute("SELECT * FROM ia_claves WHERE activo = 1 ORDER BY orden, id")]
        if not claves:
            raise IAError("No hay claves de IA activas")
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
