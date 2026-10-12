"""
problemas.py — Ónix FM
Panel de problemas del Studio: todo lo que puede dejar mal el aire, en un
solo lugar y sin depender de un aviso que desaparece a los 5 segundos.

    GET  /api/v1/problemas         → {"activos": [...], "recientes": [...], "no_vistos": n}
    POST /api/v1/problemas/vistos  → marca los recientes como vistos

ACTIVOS: revisiones que se recalculan (cada REVISION_SEG como mucho):
    motor detenido o con código anterior · archivos que faltan en disco ·
    relojes en uso con filas sin audios · eventos sin audios · cuñas con el
    audio desactivado · tandas planificadas demasiado largas · lista de
    respaldo vacía · biblioteca demasiado chica para el automático.
RECIENTES: lo que pasó al aire (aire muerto, errores de reproducción,
    cuñas que no salieron, respaldo, motor trabado). Se guardan en memoria
    (los últimos 200): al reiniciar ONIX se pierden, pero quedan en el log.

Niveles: "critico" (el aire está o puede quedar mal ahora), "error", "aviso".
"""

import itertools
import json
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime

from fastapi import APIRouter

from backend.paths import AUDIO_EXTS, DB_PATH, RESPALDO_DIR, resolve_media
from backend.version import VERSION

router = APIRouter(tags=["Problemas"])

# api_server.py asigna aquí el motor (local o remoto) al arrancar
engine = None

REVISION_SEG = 60          # las revisiones de disco no se repiten más seguido
MAX_EJEMPLOS = 15
MUSICA_MINIMA = 40         # menos canciones que esto: el automático repetirá mucho


# ── Lo que pasó (eventos del motor) ─────────────────────────────────────────

_lock = threading.Lock()
_recientes: deque = deque(maxlen=200)
_ids = itertools.count(1)
_visto_hasta = 0


def _fmt(seg) -> str:
    seg = int(round(float(seg or 0)))
    return f"{seg // 60}:{seg % 60:02d}"


def _describir(evento: str, d: dict):
    """(nivel, mensaje) de un evento del motor, o None si no es un problema."""
    if evento == "aire_muerto":
        return "critico", f"AIRE MUERTO · {d.get('msg', '')}"
    if evento == "playback_error":
        return "error", d.get("msg") or "Error de reproducción"
    if evento == "pauta_missed":
        return "error", f"La cuña de {d.get('cliente', '¿?')} no salió en su hora ({d.get('slot', '')})"
    if evento == "respaldo_activado":
        return "error", f"Sonó la lista de respaldo ({d.get('archivo', '')}): no había nada programado que sonara"
    if evento == "tanda_excedida":
        return "aviso", (f"La tanda :{int(d.get('minuto', 0)):02d} duró {_fmt(d.get('duracion'))}"
                         f" (máximo {_fmt(d.get('maximo'))})")
    if evento == "watchdog_stall":
        return "error", "El motor se trabó y reinició la reproducción"
    if evento == "engine_stopped":
        return "critico", "Se perdió la conexión con el motor de audio"
    return None


def registrar(evento: str, datos: dict):
    """Lo llama el puente de eventos del motor (desde hilos del motor)."""
    desc = _describir(evento, datos or {})
    if not desc:
        return
    nivel, msg = desc
    with _lock:
        _recientes.append({"id": next(_ids), "ts": datetime.now().isoformat(timespec="seconds"),
                           "tipo": evento, "nivel": nivel, "msg": msg})


# ── Lo que está mal ahora (revisiones) ──────────────────────────────────────

_cache = {"t": 0.0, "activos": []}


def _p(clave, nivel, titulo, detalle="", ejemplos=None, donde=""):
    return {"clave": clave, "nivel": nivel, "titulo": titulo, "detalle": detalle,
            "ejemplos": ejemplos or [], "donde": donde}


def _revisar_motor() -> list:
    if engine is None:
        return [_p("motor", "critico", "Motor de audio no disponible")]
    try:
        st = engine.status
    except Exception as exc:                       # motor remoto caído
        return [_p("motor", "critico", "Sin conexión con el motor de audio", str(exc))]
    out = []
    if not st.get("running"):
        out.append(_p("motor", "critico", "El motor de audio está detenido",
                      st.get("last_error") or "", donde="Admin › Motor de audio"))
    if st.get("version") != VERSION:
        out.append(_p("version", "critico", "El motor de audio tiene código anterior",
                      f"motor {st.get('version') or 'sin versión'} · servidor {VERSION}. "
                      "Cierra todas las ventanas de ONIX y ábrelo con «Actualizar ONIX.bat»."))
    try:
        tandas = [i for i in engine.programa().get("items", []) if i.get("tipo") == "tanda" and i.get("excede")]
    except Exception:
        tandas = []
    if tandas:
        out.append(_p("tandas_largas", "aviso", f"{len(tandas)} tanda(s) planificada(s) más larga(s) de lo permitido",
                      "Revisa cuántas cuñas tiene cada hora o sube la duración máxima.",
                      [f"{t.get('hora', '')[:5]} · {t.get('titulo')} · {_fmt(t.get('duracion'))}" for t in tandas[:MAX_EJEMPLOS]],
                      "Admin › Pautas / Motor de audio"))
    return out


def _revisar_bd(conn) -> list:
    out = []
    # Archivos que faltan en disco
    faltan = []
    for r in conn.execute("SELECT id, titulo, artista, tipo_audio, archivo_path FROM audios WHERE activo = 1"):
        try:
            if not resolve_media(r["archivo_path"] or "").is_file():
                faltan.append(f"#{r['id']} {r['titulo']} — {r['artista'] or ''} ({r['tipo_audio']})")
        except (OSError, ValueError):
            faltan.append(f"#{r['id']} {r['titulo']}")
    if faltan:
        out.append(_p("archivos", "error", f"{len(faltan)} audio(s) activo(s) sin archivo en el disco",
                      "Se saltarán al aire. Vuelve a subirlos o desactívalos en la biblioteca.",
                      faltan[:MAX_EJEMPLOS], "Admin › Biblioteca"))

    # Relojes en uso (grilla o eventos) con filas que no tienen audios
    from backend.audio.planner import Planner
    p = Planner(DB_PATH)
    en_uso = {r[0] for r in conn.execute("SELECT DISTINCT reloj_id FROM reloj_grilla")}
    try:
        en_uso |= {r[0] for r in conn.execute("SELECT DISTINCT reloj_id FROM reloj_eventos WHERE activo = 1")}
    except sqlite3.OperationalError:
        pass
    vacias = []
    for rel in conn.execute("SELECT id, nombre FROM relojes"):
        if rel["id"] not in en_uso:
            continue
        for i, f in enumerate(conn.execute(
                "SELECT tipo_audio, filtros, audio_id FROM reloj_filas WHERE reloj_id = ? ORDER BY orden, id",
                (rel["id"],)).fetchall()):
            if f["audio_id"]:
                n = conn.execute("SELECT COUNT(*) FROM audios WHERE id = ? AND activo = 1",
                                 (f["audio_id"],)).fetchone()[0]
            else:
                try:
                    filtros = json.loads(f["filtros"] or "{}")
                except ValueError:
                    filtros = {}
                clauses, params = p._where_filtros(filtros if isinstance(filtros, dict) else {})
                where = " AND ".join(["a.activo = 1", "a.tipo_audio = ?"] + clauses)
                n = conn.execute(f"SELECT COUNT(*) FROM audios a WHERE {where}",
                                 [f["tipo_audio"]] + params).fetchone()[0]
            if not n:
                vacias.append(f"{rel['nombre']} · fila {i + 1} ({f['tipo_audio']})")
    if vacias:
        out.append(_p("relojes", "aviso", f"{len(vacias)} fila(s) de relojes en uso sin audios disponibles",
                      "Esas filas se saltan al planificar: la hora no suena como la diseñaste.",
                      vacias[:MAX_EJEMPLOS], "Admin › Relojes"))

    # Cuñas vigentes con el audio desactivado o borrado
    hoy = datetime.now().date().isoformat()
    cunas = [f"{r['cliente']} (pauta #{r['id']})" for r in conn.execute(
        """SELECT p.id, p.cliente FROM pautas p LEFT JOIN audios a ON a.id = p.audio_id
           WHERE p.activo = 1 AND (a.id IS NULL OR a.activo = 0)
             AND (COALESCE(p.fecha_fin, '') = '' OR p.fecha_fin >= ?)""", (hoy,))]
    if cunas:
        out.append(_p("pautas", "error", f"{len(cunas)} pauta(s) vigente(s) con el audio desactivado o borrado",
                      "Esas cuñas no van a salir y el cliente las está pagando.",
                      cunas[:MAX_EJEMPLOS], "Admin › Pautas"))

    # Bandeja de entrada: música cargada que aún no sale al aire
    try:
        pend = conn.execute("SELECT COUNT(*) FROM audios WHERE activo = 1 AND tipo_audio = 'Musica'"
                            " AND aprobada = 0").fetchone()[0]
    except sqlite3.OperationalError:
        pend = 0
    if pend:
        out.append(_p("bandeja", "aviso", f"{pend} canción(es) en la Bandeja de entrada",
                      "No suenan en automático hasta asignarles género y rotación y aprobarlas.",
                      donde="Admin › Biblioteca"))

    # Biblioteca chica para el automático
    musica = conn.execute("SELECT COUNT(*) FROM audios WHERE activo = 1 AND tipo_audio = 'Musica'").fetchone()[0]
    if musica < MUSICA_MINIMA:
        out.append(_p("biblioteca", "aviso", f"Solo {musica} canciones activas",
                      f"Con menos de {MUSICA_MINIMA} el automático repetirá canciones muy seguido.",
                      donde="Admin › Biblioteca"))
    return out


def _revisar_respaldo() -> list:
    try:
        hay = any(p.is_file() and p.suffix.lower() in AUDIO_EXTS for p in RESPALDO_DIR.rglob("*"))
    except OSError:
        hay = False
    if hay:
        return []
    return [_p("respaldo", "aviso", "La lista de respaldo está vacía",
               f"Si todo lo demás falla, ONIX no tendrá qué poner y habrá silencio. "
               f"Copia algunas canciones a: {RESPALDO_DIR}")]


def _revisar_respaldos_bd() -> list:
    from backend.api import respaldos
    out = []
    a = respaldos.ajustes()
    edad = respaldos.edad_ultimo_h()
    if respaldos.estado["ultimo_error"]:
        out.append(_p("respaldo_bd", "error", "El último respaldo de la base de datos falló",
                      respaldos.estado["ultimo_error"], donde="Admin › Motor de audio"))
    elif edad is None or edad > 48:
        out.append(_p("respaldo_bd", "error", "No hay un respaldo reciente de la base de datos",
                      "Relojes, pautas, marcas de los audios e historial se perderían si falla el disco. "
                      "ONIX respalda solo una vez por día mientras esté encendido.",
                      donde="Admin › Motor de audio"))
    if respaldos.mismo_disco(a["carpeta"]):
        out.append(_p("respaldo_disco", "aviso", "Los respaldos están en el mismo disco que ONIX",
                      f"Si ese disco falla se pierden los dos. Elige una carpeta en otro disco, un USB "
                      f"o una carpeta sincronizada con la nube (ahora: {a['carpeta']}).",
                      donde="Admin › Motor de audio"))
    return out


def revisar(forzar: bool = False) -> list:
    ahora = time.monotonic()
    if forzar or ahora - _cache["t"] >= REVISION_SEG:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            bd = _revisar_bd(conn)
        finally:
            conn.close()
        _cache.update(t=ahora, activos=bd + _revisar_respaldo() + _revisar_respaldos_bd())
    # El motor se revisa siempre (es barato y es lo más urgente)
    orden = {"critico": 0, "error": 1, "aviso": 2}
    return sorted(_revisar_motor() + _cache["activos"], key=lambda x: orden[x["nivel"]])


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/api/v1/problemas", summary="Problemas activos y recientes")
def problemas(forzar: bool = False):
    activos = revisar(forzar)
    with _lock:
        recientes = list(reversed(_recientes))
        visto = _visto_hasta
    for r in recientes:
        r["visto"] = r["id"] <= visto
    return {"activos": activos, "recientes": recientes,
            "no_vistos": sum(1 for r in recientes if not r["visto"])}


@router.post("/api/v1/problemas/vistos", summary="Marcar los recientes como vistos")
def marcar_vistos():
    global _visto_hasta
    with _lock:
        _visto_hasta = _recientes[-1]["id"] if _recientes else _visto_hasta
    return {"ok": True}
